
## Part (b): Data Structures Used and Reasons for Their Use

The system is broken into four modules (see `algorithm.md`): Slot Display,
Vehicle Entry, Fee Calculation, and Payment & Barrier Control. Each relies
on a small set of data structures chosen specifically for the time
complexity each operation needs.

---

### 1. Min-Heap (Priority Queue) — pool of free slot IDs

**Used in:** Slot Availability & Display, Vehicle Entry, Payment & Barrier Control

```
availableSlots = min-heap of {1, 2, 3, ..., N}
```

**Why a heap:**
- The size of the heap gives the **count of free slots** in O(1) — needed
  continuously by the entrance display.
- Popping the minimum always assigns the **lowest-numbered / nearest**
  free slot to an arriving vehicle, in O(log N).
- Pushing a slot back when a vehicle exits is also O(log N).
- A plain unsorted list would make "find the lowest free slot" an O(N)
  scan every time a car enters — unacceptable once the lot has hundreds
  of slots.

*Alternative considered:* a queue/stack (deque) of free slot IDs gives
O(1) allocate/free instead of O(log N), but doesn't guarantee handing out
the nearest slot first — a reasonable trade-off in some deployments, but
the heap was chosen here because "nearest slot first" is what a real
driver expects from a well-run parking system.

---

### 2. Hash Map — active tickets, keyed by number plate

**Used in:** Vehicle Entry, Fee Calculation, Payment & Barrier Control

```
activeTickets: plate_number -> Ticket{slotId, entryTime, vehicleType}
```

**Why a hash map:**
- When a vehicle wants to exit, the system must find *its* entry record
  instantly. A hash map gives O(1) lookup, insert, and delete by plate
  number.
- The alternative — storing tickets in a list and searching for a
  matching plate — would be O(N), which gets slower as more cars are
  parked at once. This defeats the purpose of an automated system meant
  to reduce queueing at the barrier.

---

### 3. Hash Map — rate card, keyed by vehicle type

**Used in:** Fee Calculation

```
rateTable: vehicleType -> (firstHourRate, extraHourRate)
```

**Why a hash map:**
- O(1) lookup of the correct billing rate for bikes, cars, or trucks.
- Keeping rates in a lookup table (rather than hard-coded `if/else`
  branches) means the client can update pricing without touching the
  core algorithm — important since parking rates change over time.

---

### 4. Record / Object Types — Vehicle, ParkingSlot, Ticket

**Used in:** all modules, as the shared "nouns" of the system

| Structure | Fields | Purpose |
|---|---|---|
| `Vehicle` | plate_number, vehicle_type | Represents the car/bike/truck being tracked |
| `ParkingSlot` | slot_id, floor_level, status | Mirrors one physical bay; status flips FREE/OCCUPIED |
| `Ticket` | plate, slot_id, vehicle_type, entry_time, ticket_id | Created at entry, consumed at exit; links a vehicle to a slot and a time window |

These are implemented as lightweight classes/dataclasses. They don't
provide algorithmic speed-up themselves, but they keep related data
bundled together so the heap and hash map above store *one object* per
vehicle/slot rather than scattered loose variables — this keeps the
in-memory model consistent with the database rows described in
`database-design.md`.

---

### Summary Table

| Structure | Holds | Key operation | Complexity | Why it matters here |
|---|---|---|---|---|
| Min-heap | free slot IDs | pop/push lowest slot | O(log N) | Fast allocate/free, O(1) free-count for the display |
| Hash map | active tickets | lookup by plate | O(1) | Instant lookup at exit, regardless of how many cars are parked |
| Hash map | rate card | lookup by vehicle type | O(1) | Fast, easily updatable fee lookup |
| Ticket/Vehicle/Slot objects | one record per entity | read/update fields | O(1) | Keeps related data together across modules |