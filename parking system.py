"""
Smart Parking Management System
--------------------------------
Data structures used (see report for rationale):
    - heapq (min-heap)  -> pool of free slot ids            -> O(log N) allocate/free, O(1) count
    - dict (hash map)   -> plate_number -> active Ticket     -> O(1) lookup on exit
    - dataclasses        -> Vehicle, ParkingSlot, Ticket representations
    - sqlite3            -> durable database matching the schema in the report
 
Run this file directly for a demo:  python parking_system.py
"""
 
import heapq
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple
 
DB_PATH = "parking_system.db"
 
# Rate card: vehicle_type -> (first_hour_rate, extra_hour_rate)
RATE_TABLE = {
    "BIKE": (10.0, 5.0),
    "CAR": (20.0, 10.0),
    "TRUCK": (30.0, 15.0),
}
 
 
# --------------------------------------------------------------------------- #
#  In-memory data structures
# --------------------------------------------------------------------------- #
@dataclass
class Vehicle:
    plate_number: str
    vehicle_type: str  # BIKE / CAR / TRUCK
 
 
@dataclass
class ParkingSlot:
    slot_id: int
    slot_number: int
    floor_level: int = 1
    status: str = "FREE"  # FREE / OCCUPIED
 
 
@dataclass
class Ticket:
    plate_number: str
    slot_id: int
    vehicle_type: str
    entry_time: datetime
    ticket_id: Optional[int] = None  # matches Transaction.ticket_id in the DB
 
 
# --------------------------------------------------------------------------- #
#  Database layer (mirrors the schema described in parking_system_report.md)
# --------------------------------------------------------------------------- #
class ParkingDB:
    def __init__(self, path: str = DB_PATH):
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
 
    def _create_schema(self):
        c = self.conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS ParkingLot (
                lot_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                address      TEXT,
                total_slots  INTEGER NOT NULL
            );
 
            CREATE TABLE IF NOT EXISTS ParkingSlot (
                slot_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                lot_id       INTEGER NOT NULL REFERENCES ParkingLot(lot_id),
                slot_number  INTEGER NOT NULL,
                floor_level  INTEGER DEFAULT 1,
                slot_type    TEXT CHECK(slot_type IN ('COMPACT','LARGE','HANDICAP')) DEFAULT 'COMPACT',
                status       TEXT CHECK(status IN ('FREE','OCCUPIED')) DEFAULT 'FREE',
                UNIQUE(lot_id, slot_number)
            );
 
            CREATE TABLE IF NOT EXISTS Vehicle (
                vehicle_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                plate_number   TEXT NOT NULL UNIQUE,
                vehicle_type   TEXT CHECK(vehicle_type IN ('BIKE','CAR','TRUCK')) NOT NULL
            );
 
            CREATE TABLE IF NOT EXISTS RateCard (
                vehicle_type      TEXT PRIMARY KEY,
                first_hour_rate   REAL NOT NULL,
                extra_hour_rate   REAL NOT NULL
            );
 
            CREATE TABLE IF NOT EXISTS ParkingTransaction (
                ticket_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id    INTEGER NOT NULL REFERENCES Vehicle(vehicle_id),
                slot_id       INTEGER NOT NULL REFERENCES ParkingSlot(slot_id),
                entry_time    DATETIME NOT NULL,
                exit_time     DATETIME,
                amount_paid   REAL,
                status        TEXT CHECK(status IN ('ACTIVE','COMPLETED')) DEFAULT 'ACTIVE'
            );
            """
        )
        c.commit()
 
    def seed_lot(self, name: str, address: str, total_slots: int) -> int:
        c = self.conn
        cur = c.execute(
            "INSERT INTO ParkingLot (name, address, total_slots) VALUES (?, ?, ?)",
            (name, address, total_slots),
        )
        lot_id = cur.lastrowid
        c.executemany(
            "INSERT INTO ParkingSlot (lot_id, slot_number) VALUES (?, ?)",
            [(lot_id, n) for n in range(1, total_slots + 1)],
        )
        c.executemany(
            "INSERT OR REPLACE INTO RateCard (vehicle_type, first_hour_rate, extra_hour_rate) VALUES (?, ?, ?)",
            [(vt, rates[0], rates[1]) for vt, rates in RATE_TABLE.items()],
        )
        c.commit()
        return lot_id
 
    def get_or_create_vehicle(self, plate: str, vehicle_type: str) -> int:
        c = self.conn
        row = c.execute("SELECT vehicle_id FROM Vehicle WHERE plate_number = ?", (plate,)).fetchone()
        if row:
            return row[0]
        cur = c.execute(
            "INSERT INTO Vehicle (plate_number, vehicle_type) VALUES (?, ?)",
            (plate, vehicle_type),
        )
        c.commit()
        return cur.lastrowid
 
    def free_slot_count(self, lot_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM ParkingSlot WHERE lot_id = ? AND status = 'FREE'", (lot_id,)
        ).fetchone()
        return row[0]
 
    def open_transaction(self, vehicle_id: int, slot_id: int, entry_time: datetime) -> int:
        c = self.conn
        cur = c.execute(
            "INSERT INTO ParkingTransaction (vehicle_id, slot_id, entry_time, status) "
            "VALUES (?, ?, ?, 'ACTIVE')",
            (vehicle_id, slot_id, entry_time.isoformat(sep=" ")),
        )
        c.execute("UPDATE ParkingSlot SET status = 'OCCUPIED' WHERE slot_id = ?", (slot_id,))
        c.commit()
        return cur.lastrowid
 
    def close_transaction(self, ticket_id: int, slot_id: int, exit_time: datetime, amount: float):
        c = self.conn
        c.execute(
            "UPDATE ParkingTransaction SET exit_time = ?, amount_paid = ?, status = 'COMPLETED' "
            "WHERE ticket_id = ?",
            (exit_time.isoformat(sep=" "), amount, ticket_id),
        )
        c.execute("UPDATE ParkingSlot SET status = 'FREE' WHERE slot_id = ?", (slot_id,))
        c.commit()
 
    def slot_db_id(self, lot_id: int, slot_number: int) -> int:
        row = self.conn.execute(
            "SELECT slot_id FROM ParkingSlot WHERE lot_id = ? AND slot_number = ?",
            (lot_id, slot_number),
        ).fetchone()
        return row[0]
 
 
# --------------------------------------------------------------------------- #
#  Core algorithm — ParkingLot facade
# --------------------------------------------------------------------------- #
class ParkingLot:
    def __init__(self, db: ParkingDB, lot_id: int, total_slots: int):
        self.db = db
        self.lot_id = lot_id
        self.total_slots = total_slots
 
        # Min-heap of free slot NUMBERS (1..N) -> O(log N) allocate/free
        self.available_slots = list(range(1, total_slots + 1))
        heapq.heapify(self.available_slots)
 
        # Hash map: plate_number -> Ticket  -> O(1) lookup on exit
        self.active_tickets: Dict[str, Ticket] = {}
 
    # ---- feature: know availability before entry -------------------------
    def check_availability(self) -> Tuple[bool, int]:
        free_count = len(self.available_slots)
        return free_count > 0, free_count
 
    # ---- feature: record vehicle on entry ---------------------------------
    def vehicle_entry(self, plate: str, vehicle_type: str) -> Ticket:
        if plate in self.active_tickets:
            raise ValueError(f"Vehicle {plate} is already parked.")
 
        available, free_count = self.check_availability()
        if not available:
            raise RuntimeError("Parking Full")
 
        slot_number = heapq.heappop(self.available_slots)  # lowest free slot id
        entry_time = datetime.now()
 
        vehicle_id = self.db.get_or_create_vehicle(plate, vehicle_type)
        slot_db_id = self.db.slot_db_id(self.lot_id, slot_number)
        ticket_id = self.db.open_transaction(vehicle_id, slot_db_id, entry_time)
 
        ticket = Ticket(
            plate_number=plate,
            slot_id=slot_number,
            vehicle_type=vehicle_type,
            entry_time=entry_time,
            ticket_id=ticket_id,
        )
        self.active_tickets[plate] = ticket
        return ticket
 
    # ---- Module 3: duration & fee calculation (no state change yet) -------
    def calculate_fee(self, plate: str) -> Tuple[Ticket, float, float]:
        """Returns (ticket, duration_in_hours, amount_due). Does NOT free the slot."""
        ticket = self.active_tickets.get(plate)
        if ticket is None:
            raise ValueError(f"No active ticket found for {plate}")
 
        exit_time = datetime.now()
        duration_seconds = (exit_time - ticket.entry_time).total_seconds()
        duration_hours_billed = max(1, math.ceil(duration_seconds / 3600))
        fee = self._compute_fee(ticket.vehicle_type, duration_hours_billed)
        return ticket, duration_seconds / 3600.0, fee
 
    # ---- Module 4: payment & barrier control -------------------------------
    def process_payment_and_exit(self, plate: str, amount_tendered: float) -> Tuple[float, float]:
        """Confirms payment, frees the slot, and 'opens the barrier'.
        Returns (duration_in_hours, amount_paid). Raises if payment is short."""
        ticket, duration_hours, fee = self.calculate_fee(plate)
 
        if amount_tendered < fee:
            raise ValueError(f"Insufficient payment: owes {fee:.2f}, received {amount_tendered:.2f}")
 
        # Payment confirmed -> release resources and signal the barrier
        self.active_tickets.pop(plate)
        exit_time = datetime.now()
        heapq.heappush(self.available_slots, ticket.slot_id)  # slot free again
 
        slot_db_id = self.db.slot_db_id(self.lot_id, ticket.slot_id)
        self.db.close_transaction(ticket.ticket_id, slot_db_id, exit_time, fee)
 
        self._open_barrier(plate)
        return duration_hours, fee
 
    @staticmethod
    def _open_barrier(plate: str):
        # Hook for hardware/IoT integration (relay signal, gate API, etc.)
        print(f"[BARRIER] Payment confirmed for {plate} -> gate opening.")
 
    @staticmethod
    def _compute_fee(vehicle_type: str, hours_billed: int) -> float:
        first_hour_rate, extra_hour_rate = RATE_TABLE[vehicle_type]
        if hours_billed <= 1:
            return first_hour_rate
        return first_hour_rate + (hours_billed - 1) * extra_hour_rate
 
 
# --------------------------------------------------------------------------- #
#  Demo
# --------------------------------------------------------------------------- #
def demo():
    db = ParkingDB()
    lot_id = db.seed_lot("Downtown Parking", "123 Main St", total_slots=5)
    lot = ParkingLot(db, lot_id, total_slots=5)
 
    print("== Checking availability before entry ==")
    available, count = lot.check_availability()
    print(f"Available: {available}, Free slots: {count}\n")
 
    print("== Vehicle entries ==")
    t1 = lot.vehicle_entry("KAA 123A", "CAR")
    print(f"KAA 123A parked in slot {t1.slot_id} at {t1.entry_time}")
    t2 = lot.vehicle_entry("KBB 456B", "TRUCK")
    print(f"KBB 456B parked in slot {t2.slot_id} at {t2.entry_time}")
 
    available, count = lot.check_availability()
    print(f"\nAvailable now: {available}, Free slots: {count}\n")
 
    # Simulate KAA 123A having parked for a while by back-dating entry_time
    lot.active_tickets["KAA 123A"].entry_time = datetime.now().replace(
        hour=max(0, datetime.now().hour - 2)
    )
 
    print("== Vehicle exit (Module 3: calculate, Module 4: pay + barrier) ==")
    _, duration, fee = lot.calculate_fee("KAA 123A")
    print(f"KAA 123A parked for {duration:.2f} hours -> Fee owed: {fee:.2f}")
    duration, paid = lot.process_payment_and_exit("KAA 123A", amount_tendered=fee)
    print(f"Payment of {paid:.2f} accepted -> slot released.")
 
    available, count = lot.check_availability()
    print(f"\nAvailable after exit: {available}, Free slots: {count}")
 
 
if __name__ == "__main__":
    demo()