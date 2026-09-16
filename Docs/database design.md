
## Part (c): Dynamic Database Design

The database is "dynamic" because slot **status** and the free-slot
**count** change automatically and continuously as vehicles enter and
exit — no manual editing is ever required, and new vehicles/transactions
can be added indefinitely without changing the schema.

---

## 1. Entity-Relationship Overview

```
ParkingLot (1) ───< (many) ParkingSlot
Vehicle    (1) ───< (many) ParkingTransaction
ParkingSlot(1) ───< (many) ParkingTransaction
RateCard   (1) ───< (many) ParkingTransaction   [related via vehicle_type]
```

A `ParkingTransaction` row is the event that links one `Vehicle` to one
`ParkingSlot` for the duration of a single visit — `entry_time` and
`exit_time` bound that visit, and `amount_paid` is filled in once Module 4
(Payment & Barrier Control) confirms payment.

---

## 2. Schema (SQL / SQLite-compatible)

```sql
CREATE TABLE ParkingLot (
    lot_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    address      TEXT,
    total_slots  INTEGER NOT NULL
);

CREATE TABLE ParkingSlot (
    slot_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id       INTEGER NOT NULL REFERENCES ParkingLot(lot_id),
    slot_number  INTEGER NOT NULL,
    floor_level  INTEGER DEFAULT 1,
    slot_type    TEXT CHECK(slot_type IN ('COMPACT','LARGE','HANDICAP')) DEFAULT 'COMPACT',
    status       TEXT CHECK(status IN ('FREE','OCCUPIED')) DEFAULT 'FREE',
    UNIQUE(lot_id, slot_number)
);

CREATE TABLE Vehicle (
    vehicle_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_number   TEXT NOT NULL UNIQUE,
    vehicle_type   TEXT CHECK(vehicle_type IN ('BIKE','CAR','TRUCK')) NOT NULL
);

CREATE TABLE RateCard (
    vehicle_type      TEXT PRIMARY KEY,
    first_hour_rate   REAL NOT NULL,   -- KES
    extra_hour_rate   REAL NOT NULL    -- KES
);

CREATE TABLE ParkingTransaction (
    ticket_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id    INTEGER NOT NULL REFERENCES Vehicle(vehicle_id),
    slot_id       INTEGER NOT NULL REFERENCES ParkingSlot(slot_id),
    entry_time    DATETIME NOT NULL,
    exit_time     DATETIME,
    amount_paid   REAL,
    status        TEXT CHECK(status IN ('ACTIVE','COMPLETED')) DEFAULT 'ACTIVE'
);
```

---

## 3. Table Descriptions

| Table | Purpose |
|---|---|
| **ParkingLot** | One row per physical parking facility. Supports the system scaling to multiple client sites in future. |
| **ParkingSlot** | One row per physical bay. `status` is the field that changes dynamically as cars enter/exit. |
| **Vehicle** | One row per unique number plate seen by the system; grows automatically as new vehicles visit. |
| **RateCard** | Billing rules per vehicle type, kept separate from code so the client can update prices independently. |
| **ParkingTransaction** | One row per visit — the audit trail of every entry and exit, with the calculated fee. |

---

## 4. Queries That Make the Database "Dynamic"

```sql
-- Module 1: live free-slot count for the entrance display
SELECT COUNT(*) FROM ParkingSlot WHERE lot_id = ? AND status = 'FREE';

-- Module 2: vehicle entry — open a transaction, flip the slot to OCCUPIED
INSERT INTO ParkingTransaction (vehicle_id, slot_id, entry_time, status)
VALUES (?, ?, CURRENT_TIMESTAMP, 'ACTIVE');

UPDATE ParkingSlot SET status = 'OCCUPIED' WHERE slot_id = ?;

-- Module 3: fee calculation — read the open transaction, no writes yet
SELECT entry_time FROM ParkingTransaction
WHERE vehicle_id = ? AND status = 'ACTIVE';

-- Module 4: payment confirmed — close the transaction, free the slot, barrier opens
UPDATE ParkingTransaction
   SET exit_time = CURRENT_TIMESTAMP, amount_paid = ?, status = 'COMPLETED'
 WHERE ticket_id = ?;

UPDATE ParkingSlot SET status = 'FREE' WHERE slot_id = ?;
```

Because the slot's `status` update happens in the same flow as every
entry/exit event, the free-slot count read by Module 1 is always accurate
— the database "moves" with the physical lot in real time, with no manual
data entry anywhere in the process.

---

## 5. Why This Design Scales

- **New vehicles** are automatically inserted the first time they're seen
  (`INSERT ... ON CONFLICT` / "get or create" pattern) — no fixed limit.
- **New lots** can be added by inserting into `ParkingLot` and generating
  matching `ParkingSlot` rows — the schema doesn't assume a single site.
- **Rate changes** only touch `RateCard`, never the transaction history —
  so past tickets keep the price that was actually charged at the time.
- **Full history** is preserved in `ParkingTransaction` even after a slot
  is freed, supporting reporting/auditing for the client (e.g. revenue
  per day, average stay duration, busiest hours).