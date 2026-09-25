"""
app.py
======
Web-based front end for the Multimedia Parking System (MMU), built with
Flask. This ties the four modules in parking_core.py to actual pages a
driver / attendant can use in a browser:

    GET  /            -> Module 1: live visual display of slot availability
    POST /entry        -> Module 2: register a vehicle on arrival
    POST /calculate     -> Module 3: show the fee owed for a plate at exit
    POST /exit          -> Module 4: confirm payment, free the slot, "open barrier"

Run with:  python app.py
Then open: http://127.0.0.1:5000 in a browser.
"""

from flask import Flask, render_template, request, redirect, url_for, flash

from parking_core import ParkingDB, ParkingLot

app = Flask(__name__)
app.secret_key = "mmu-parking-demo-secret"  # only used to sign flash messages

# --------------------------------------------------------------------------- #
#  One shared parking lot for the whole app (single-site demo).
#  On restart, ParkingLot rebuilds its heap/dict from the database, so
#  nothing is lost even if the server is stopped and started again.
# --------------------------------------------------------------------------- #
db = ParkingDB()
LOT_ID = db.get_or_create_lot(name="MMU Main Parking", address="Multimedia University of Kenya", total_slots=12)
lot = ParkingLot(db, LOT_ID, total_slots=12)


@app.route("/")
def dashboard():
    """Module 1: visual display of parking slots available before entry."""
    status = lot.get_display_status()
    return render_template("index.html", status=status)


@app.route("/entry", methods=["POST"])
def entry():
    """Module 2: record a vehicle on arrival and assign it a slot."""
    plate = request.form.get("plate", "")
    vehicle_type = request.form.get("vehicle_type", "CAR")

    try:
        ticket = lot.vehicle_entry(plate, vehicle_type)
        flash(f"Welcome {ticket.plate_number}! Proceed to Slot {ticket.slot_id}.", "success")
    except (ValueError, RuntimeError) as e:
        flash(str(e), "error")

    return redirect(url_for("dashboard"))


@app.route("/calculate", methods=["POST"])
def calculate():
    """Module 3: calculate (but do not yet charge) the fee owed for a plate."""
    plate = request.form.get("plate", "")

    try:
        ticket, duration_minutes, fee = lot.calculate_fee(plate)
        status = lot.get_display_status()
        return render_template(
            "index.html",
            status=status,
            quote={
                "plate": ticket.plate_number,
                "slot": ticket.slot_id,
                "duration_minutes": round(duration_minutes, 1),
                "fee": fee,
            },
        )
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("dashboard"))


@app.route("/exit", methods=["POST"])
def exit_vehicle():
    """Module 4: confirm payment, free the slot, and 'open the barrier'."""
    plate = request.form.get("plate", "")
    amount_paid = request.form.get("amount_paid", "0")

    try:
        amount_paid = float(amount_paid)
        duration_minutes, fee = lot.process_payment_and_exit(plate, amount_paid)
        flash(
            f"Payment of Ksh {fee} received for {plate.strip().upper()} "
            f"({duration_minutes:.1f} min parked). Barrier opening — safe travels!",
            "success",
        )
    except ValueError as e:
        flash(str(e), "error")

    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    # debug=True is fine for local development/demo; turn off for production.
    app.run(debug=True, use_reloader=False)