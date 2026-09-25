# Data Structures and Algorithms — Task One
## Modern Parking System (Kenya)

## 1. Analysis of the Client's Terms of Reference

Re-reading the brief, the client needs the system to do four distinct things:

| Client requirement | Implied module |
|---|---|
| "Drivers to see (visual display) of parking slots available **before entry**" | **Module 1: Slot Availability & Display** |
| "System records vehicles **on arrival**" | **Module 2: Vehicle Entry / Registration** |
| "On exit, system automatically calculates **total time spent** and **amount to pay**" | **Module 3: Duration & Fee Calculation** |
| "**Barrier opens** to allow exit on payment of parking fees" | **Module 4: Payment & Barrier Control** |

These four modules map directly onto the full lifecycle of a vehicle in the
lot: *check → enter → park → exit (pay + barrier)*. Each is designed,
algorithmed, and data-structured separately below, then tied together by one
shared in-memory model and one shared database (Section 4).

---

## 2. Module Algorithms

### Module 1 — Slot Availability & Display
**Purpose:** Give drivers a real-time visual count of free slots before they enter, so they don't queue for a full lot.

```
FUNCTION getDisplayStatus():
    freeCount = size(availableSlots)          # O(1)
    totalSlots = N
    status = "FULL" IF freeCount == 0 ELSE "AVAILABLE"
    RETURN {status, freeCount, totalSlots}     # pushed to the entrance screen/app
```
This function is called on a timer (e.g. every 2 seconds) or triggered
whenever Module 2/3 change slot state, so the entrance display always
reflects the live count.

### Module 2 — Vehicle Entry / Registration
**Purpose:** Record the vehicle the instant it arrives and assign it a slot.

```
FUNCTION vehicleEntry(plate, vehicleType):
    IF activeTickets.contains(plate):
        RETURN ERROR "Vehicle already parked"

    IF isEmpty(availableSlots):
        RETURN ERROR "Parking Full"            # Module 1 would already show this

    slotId    = heap_pop_min(availableSlots)    # nearest/lowest free slot, O(log N)
    entryTime = now()
    ticket    = Ticket(plate, slotId, entryTime, vehicleType)

    activeTickets.put(plate, ticket)            # O(1)
    DB.insert(vehicle, slot_status = OCCUPIED, transaction row with entry_time)

    RETURN ticket    # shown to driver: "Proceed to Slot 7"
```

### Module 3 — Duration & Fee Calculation
**Purpose:** At exit, work out how long the vehicle stayed and what it owes.

```
FUNCTION calculateFee(plate):
    IF NOT activeTickets.contains(plate):
        RETURN ERROR "Ticket not found"

    ticket   = activeTickets.get(plate)          # O(1), NOT removed yet
    exitTime = now()
    duration = exitTime - ticket.entryTime        # seconds
    hours    = CEIL(duration / 3600)              # partial hour billed as full hour
    fee      = computeFee(ticket.vehicleType, hours)

    RETURN (ticket, duration, fee)                # passed to Module 4 for payment


FUNCTION computeFee(vehicleType, hoursBilled):
    (firstHourRate, extraHourRate) = rateTable[vehicleType]
    IF hoursBilled <= 1:
        RETURN firstHourRate
    RETURN firstHourRate + (hoursBilled - 1) * extraHourRate
```
Fee calculation is deliberately separated from payment/exit (Module 4) —
the client's own wording implies two events: the system *calculates* the
fee, and only *after payment* does the barrier open.

### Module 4 — Payment & Barrier Control
**Purpose:** Confirm payment, then physically free the slot and open the barrier.

```
FUNCTION processPaymentAndExit(plate, amountTendered):
    (ticket, duration, fee) = calculateFee(plate)  # Module 3

    IF amountTendered < fee:
        RETURN ERROR "Insufficient payment"        # barrier stays shut

    activeTickets.remove(plate)                     # O(1)
    heap_push(availableSlots, ticket.slotId)         # slot free again, O(log N)

    DB.update(transaction: exit_time, amount_paid = fee, status = COMPLETED)
    DB.update(slot: status = FREE)

    openBarrier()                                    # hardware/signal trigger
    RETURN "Exit approved. Thank you."
```
`openBarrier()` is a hook to the physical gate (relay/IoT signal in a real
deployment); in software it's simply the point after which the slot count
increments and Module 1's display updates automatically.

### Complexity Summary
| Module | Operation | Time |
|---|---|---|
| 1 | Display status | O(1) |
| 2 | Vehicle entry | O(log N) |
| 3 | Fee calculation | O(1) |
| 4 | Payment + barrier + slot release | O(log N) |

---

## 3. Data Structures Used (and why)

| Structure | Module(s) | Why this structure |
|---|---|---|
| **Min-heap** of free slot IDs | 1, 2, 4 | Gives free-slot **count** in O(1) for the display, and always hands out the lowest/nearest free slot in O(log N) on entry; pushes a slot back just as cheaply on exit. |
| **Hash map** (`plate -> Ticket`) | 2, 3, 4 | O(1) lookup of an active vehicle's slot/entry time when it's time to calculate its fee or process its exit — no need to scan every parked car. |
| **Ticket / Transaction object** | 2, 3, 4 | Carries plate, slot id, vehicle type, entry time between modules without re-querying the database each time. |
| **Rate table (hash map)** | 3 | O(1) lookup of the fee structure per vehicle type (bike/car/truck), decoupled from code so rates can change without touching the algorithm. |

---

## 4. Dynamic Database Design

The database is "dynamic" in the sense that slot **status** and the count of
free slots change continuously and automatically as Modules 2 and 4 run —
no manual editing — and rate/vehicle records can grow indefinitely.

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

### Queries that make it "dynamic" (auto-updating, no manual edits)
```sql
-- Module 1: live free-slot count for the display
SELECT COUNT(*) FROM ParkingSlot WHERE lot_id = ? AND status = 'FREE';

-- Module 2: entry — insert transaction, flip slot to OCCUPIED
INSERT INTO ParkingTransaction (vehicle_id, slot_id, entry_time, status)
VALUES (?, ?, CURRENT_TIMESTAMP, 'ACTIVE');
UPDATE ParkingSlot SET status = 'OCCUPIED' WHERE slot_id = ?;

-- Module 4: payment confirmed — close transaction, flip slot to FREE, barrier opens
UPDATE ParkingTransaction
   SET exit_time = CURRENT_TIMESTAMP, amount_paid = ?, status = 'COMPLETED'
 WHERE ticket_id = ?;
UPDATE ParkingSlot SET status = 'FREE' WHERE slot_id = ?;
```

Because `ParkingSlot.status` is updated inside the same transaction as every
entry/exit event, the count read by Module 1 is always correct — the
database "moves" with the physical lot in real time.
