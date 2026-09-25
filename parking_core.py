"""
parking_core.py
================
Core logic for the Multimedia Parking System (MMU).

This file implements the four modules identified from the client's terms
of reference:

    Module 1 - Slot Availability & Display  -> ParkingLot.get_display_status()
    Module 2 - Vehicle Entry / Registration  -> ParkingLot.vehicle_entry()
    Module 3 - Duration & Fee Calculation    -> ParkingLot.calculate_fee()
    Module 4 - Payment & Barrier Control     -> ParkingLot.process_payment_and_exit()

Data structures used (see docs/data-structures.md for full rationale):
    - heapq (min-heap)  -> pool of free slot numbers  -> O(log N) allocate/free, O(1) free-count
    - dict (hash map)   -> plate_number -> active Ticket -> O(1) lookup on exit
    - dataclasses        -> Vehicle, Ticket representations
    - sqlite3            -> durable database (see docs/database-design.md for schema)

Fee structure (as given by the client):
    Up to 30 minutes  -> FREE   (Ksh 0)
    Up to 2 hours     -> Ksh 50
    Up to 4 hours     -> Ksh 100
    Up to 6 hours     -> Ksh 300
    Over 6 hours      -> Ksh 500
"""

import heapq
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple

DB_PATH = "parking_system.db"


# --------------------------------------------------------------------------- #
#  In-memory data structures
# --------------------------------------------------------------------------- #
@dataclass
class Ticket:
    """One active parking session: links a plate to a slot and an entry time."""
    plate_number: str
    slot_id: int  # slot NUMBER (1..N), not the DB primary key
    vehicle_type: str
    entry_time: datetime
    ticket_id: Optional[int] = None  # matches ParkingTransaction.ticket_id in the DB


# --------------------------------------------------------------------------- #
#  Fee calculation (Module 3 helper) - implements the client's exact tiers
# --------------------------------------------------------------------------- #
def compute_fee(duration_minutes: float) -> int:
    """
    Flat-tier fee schedule given by the client. Duration is billed on the
    upper bound of the bracket it falls into (e.g. 45 minutes -> the
    'up to 2 hours' bracket, Ksh 50), regardless of vehicle type.
    """
    if duration_minutes <= 30:
        return 0
    elif duration_minutes <= 120:      # up to 2 hours
        return 50
    elif duration_minutes <= 240:      # up to 4 hours
        return 100
    elif duration_minutes <= 360:      # up to 6 hours
        return 300
    else:                              # over 6 hours
        return 500


# --------------------------------------------------------------------------- #
#  Database layer (mirrors docs/database-design.md)
# --------------------------------------------------------------------------- #
class ParkingDB:
    def __init__(self, path: str = DB_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self):
        self.conn.executescript(
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
                status       TEXT CHECK(status IN ('FREE','OCCUPIED')) DEFAULT 'FREE',
                UNIQUE(lot_id, slot_number)
            );

            CREATE TABLE IF NOT EXISTS Vehicle (
                vehicle_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                plate_number   TEXT NOT NULL UNIQUE,
                vehicle_type   TEXT NOT NULL
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
        self.conn.commit()

    def get_or_create_lot(self, name: str, address: str, total_slots: int) -> int:
        """Seeds the lot + its slots only the first time the app ever runs."""
        row = self.conn.execute("SELECT lot_id FROM ParkingLot LIMIT 1").fetchone()
        if row:
            return row[0]

        cur = self.conn.execute(
            "INSERT INTO ParkingLot (name, address, total_slots) VALUES (?, ?, ?)",
            (name, address, total_slots),
        )
        lot_id = cur.lastrowid
        self.conn.executemany(
            "INSERT INTO ParkingSlot (lot_id, slot_number) VALUES (?, ?)",
            [(lot_id, n) for n in range(1, total_slots + 1)],
        )
        self.conn.commit()
        return lot_id

    def get_or_create_vehicle(self, plate: str, vehicle_type: str) -> int:
        row = self.conn.execute(
            "SELECT vehicle_id FROM Vehicle WHERE plate_number = ?", (plate,)
        ).fetchone()
        if row:
            return row[0]
        cur = self.conn.execute(
            "INSERT INTO Vehicle (plate_number, vehicle_type) VALUES (?, ?)",
            (plate, vehicle_type),
        )
        self.conn.commit()
        return cur.lastrowid

    def slot_db_id(self, lot_id: int, slot_number: int) -> int:
        row = self.conn.execute(
            "SELECT slot_id FROM ParkingSlot WHERE lot_id = ? AND slot_number = ?",
            (lot_id, slot_number),
        ).fetchone()
        return row[0]

    def free_slot_numbers(self, lot_id: int):
        """Used on startup to rebuild the in-memory heap from durable state."""
        rows = self.conn.execute(
            "SELECT slot_number FROM ParkingSlot WHERE lot_id = ? AND status = 'FREE'",
            (lot_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def occupied_slots(self, lot_id: int):
        """Used on startup to rebuild active_tickets from durable state after a restart."""
        rows = self.conn.execute(
            """
            SELECT v.plate_number, s.slot_number, v.vehicle_type, t.entry_time, t.ticket_id
            FROM ParkingTransaction t
            JOIN Vehicle v ON v.vehicle_id = t.vehicle_id
            JOIN ParkingSlot s ON s.slot_id = t.slot_id
            WHERE s.lot_id = ? AND t.status = 'ACTIVE'
            """,
            (lot_id,),
        ).fetchall()
        return rows

    def open_transaction(self, vehicle_id: int, slot_id: int, entry_time: datetime) -> int:
        cur = self.conn.execute(
            "INSERT INTO ParkingTransaction (vehicle_id, slot_id, entry_time, status) "
            "VALUES (?, ?, ?, 'ACTIVE')",
            (vehicle_id, slot_id, entry_time.isoformat(sep=" ")),
        )
        self.conn.execute("UPDATE ParkingSlot SET status = 'OCCUPIED' WHERE slot_id = ?", (slot_id,))
        self.conn.commit()
        return cur.lastrowid

    def close_transaction(self, ticket_id: int, slot_id: int, exit_time: datetime, amount: float):
        self.conn.execute(
            "UPDATE ParkingTransaction SET exit_time = ?, amount_paid = ?, status = 'COMPLETED' "
            "WHERE ticket_id = ?",
            (exit_time.isoformat(sep=" "), amount, ticket_id),
        )
        self.conn.execute("UPDATE ParkingSlot SET status = 'FREE' WHERE slot_id = ?", (slot_id,))
        self.conn.commit()


# --------------------------------------------------------------------------- #
#  ParkingLot facade - the four modules
# --------------------------------------------------------------------------- #
class ParkingLot:
    def __init__(self, db: ParkingDB, lot_id: int, total_slots: int):
        self.db = db
        self.lot_id = lot_id
        self.total_slots = total_slots

        # Module 1/2/4 data structure: min-heap of currently FREE slot numbers
        self.available_slots = db.free_slot_numbers(lot_id)
        heapq.heapify(self.available_slots)

        # Module 2/3/4 data structure: active tickets keyed by plate number
        self.active_tickets: Dict[str, Ticket] = {}
        for plate, slot_number, vehicle_type, entry_time, ticket_id in db.occupied_slots(lot_id):
            self.active_tickets[plate] = Ticket(
                plate_number=plate,
                slot_id=slot_number,
                vehicle_type=vehicle_type,
                entry_time=datetime.fromisoformat(entry_time),
                ticket_id=ticket_id,
            )

    # ---- Module 1: Slot Availability & Display -----------------------------
    def get_display_status(self) -> dict:
        free_count = len(self.available_slots)
        occupied_plates = {t.slot_id for t in self.active_tickets.values()}
        slots = [
            {"number": n, "status": "OCCUPIED" if n in occupied_plates else "FREE"}
            for n in range(1, self.total_slots + 1)
        ]
        return {
            "total": self.total_slots,
            "free": free_count,
            "occupied": self.total_slots - free_count,
            "slots": slots,
        }

    # ---- Module 2: Vehicle Entry / Registration -----------------------------
    def vehicle_entry(self, plate: str, vehicle_type: str) -> Ticket:
        plate = plate.strip().upper()
        if plate in self.active_tickets:
            raise ValueError(f"Vehicle {plate} is already parked.")
        if not self.available_slots:
            raise RuntimeError("Parking Full. No slots available.")

        slot_number = heapq.heappop(self.available_slots)  # nearest free slot, O(log N)
        entry_time = datetime.now()

        vehicle_id = self.db.get_or_create_vehicle(plate, vehicle_type)
        slot_db_id = self.db.slot_db_id(self.lot_id, slot_number)
        ticket_id = self.db.open_transaction(vehicle_id, slot_db_id, entry_time)

        ticket = Ticket(plate, slot_number, vehicle_type, entry_time, ticket_id)
        self.active_tickets[plate] = ticket
        return ticket

    # ---- Module 3: Duration & Fee Calculation -------------------------------
    def calculate_fee(self, plate: str) -> Tuple[Ticket, float, int]:
        """Returns (ticket, duration_minutes, fee). Does NOT change any state."""
        plate = plate.strip().upper()
        ticket = self.active_tickets.get(plate)
        if ticket is None:
            raise ValueError(f"No active ticket found for {plate}")

        duration_minutes = (datetime.now() - ticket.entry_time).total_seconds() / 60.0
        fee = compute_fee(duration_minutes)
        return ticket, duration_minutes, fee

    # ---- Module 4: Payment & Barrier Control --------------------------------
    def process_payment_and_exit(self, plate: str, amount_tendered: float) -> Tuple[float, int]:
        """Confirms payment, frees the slot, and 'opens the barrier'."""
        ticket, duration_minutes, fee = self.calculate_fee(plate)

        if amount_tendered < fee:
            raise ValueError(f"Insufficient payment: owes Ksh {fee}, received Ksh {amount_tendered}")

        self.active_tickets.pop(plate)
        exit_time = datetime.now()
        heapq.heappush(self.available_slots, ticket.slot_id)  # slot free again

        slot_db_id = self.db.slot_db_id(self.lot_id, ticket.slot_id)
        self.db.close_transaction(ticket.ticket_id, slot_db_id, exit_time, fee)

        return duration_minutes, fee