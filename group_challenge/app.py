# Flask App Enhancements for Split Expense

from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from bson import ObjectId
from twilio.rest import Client
from datetime import datetime
import os

# App & Config
app = Flask(__name__)
app.secret_key = "your_secret_key"
UPLOAD_FOLDER = "static/payments"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# MongoDB Setup
mongo_client = MongoClient("mongodb://localhost:27017/")
mongo_db = mongo_client["wealthyfyme"]
split_expenses_col = mongo_db["split_expenses"]

# Twilio Config
TWILIO_ACCOUNT_SID = 'AC3dd54a862fc10fcf2284f9508cb56a24'
TWILIO_AUTH_TOKEN = '824084d4dd7119146c3e23841438597'
TWILIO_PHONE_NUMBER = '+16814413892'
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

@app.route("/split_expense", methods=["GET", "POST"])
def split_expense():
    if request.method == "POST":
        payer = request.form["payer_email"].strip()
        participants = [email.strip() for email in request.form["participant_emails"].split(",") if email.strip()]
        amount = float(request.form["amount"])

        if not participants or amount <= 0:
            flash("Invalid input.")
            return redirect(url_for("split_expense"))

        split_amount = round(amount / (len(participants) + 1), 2)
        timestamp = datetime.now()

        split_expenses_col.insert_one({
            "email": payer,
            "role": "payer",
            "participants": participants,
            "amount": amount,
            "split_amount": split_amount,
            "timestamp": timestamp
        })

        for email in participants:
            split_expenses_col.insert_one({
                "email": email,
                "role": "participant",
                "payer": payer,
                "amount": amount,
                "split_amount": split_amount,
                "status": "unpaid",
                "timestamp": timestamp
            })

            try:
                twilio_client.messages.create(
                    body=f"You owe ₹{split_amount} to {payer}.",
                    from_=TWILIO_PHONE_NUMBER,
                    to="+917876134701"  # demo number
                )
            except Exception as e:
                print(f"SMS failed for {email}: {e}")

        flash("Expense split successfully!")
        return redirect(url_for("split_expense"))

    return render_template("split_expense.html")

from flask import jsonify

@app.route("/my_splits")
def my_splits():
    user_email = "ashish123@gmail.com"  # replace with session.get('email') in real

    filter_type = request.args.get("type")  # 'owe' or 'paid'
    query = {"email": user_email}

    if filter_type == "owe":
        query["role"] = "participant"
        query["status"] = {"$ne": "paid"}
    elif filter_type == "paid":
        # Show participant with status=paid OR payer entries
        query["$or"] = [
            {"email": user_email, "role": "participant", "status": "paid"},
            {"email": user_email, "role": "payer"}
        ]

    my_records = list(split_expenses_col.find(query).sort("timestamp", -1))
    # Calculate monthly summary (bonus)
    from datetime import datetime, timedelta
    now = datetime.now()
    month_start = datetime(now.year, now.month, 1)
    monthly_records = list(split_expenses_col.find({
        "email": user_email,
        "timestamp": {"$gte": month_start}
    }))

    total_owed = sum(r.get("split_amount", 0) for r in monthly_records if r.get("role") == "participant" and r.get("status") != "paid")
    total_paid = sum(float(r.get("paid_amount", 0)) for r in monthly_records if r.get("status") == "paid")

    return render_template("my_splits.html", splits=my_records, filter_type=filter_type, total_owed=total_owed, total_paid=total_paid)


@app.route("/mark_paid/<split_id>", methods=["POST"])
def mark_paid(split_id):
    record = split_expenses_col.find_one({"_id": ObjectId(split_id)})
    if not record:
        flash("Record not found.", "danger")
        return redirect(url_for("my_splits"))

    try:
        amount_paid = float(request.form["paid_amount"])
    except (KeyError, ValueError):
        flash("Invalid payment amount.", "danger")
        return redirect(url_for("my_splits"))

    expected_amount = float(record.get("split_amount", 0))

    if amount_paid != expected_amount:
        flash(f"Please pay the exact amount: ₹{expected_amount}", "warning")
        return redirect(url_for("my_splits"))

    file = request.files.get("payment_image")
    img_path = ""

    if file and file.filename != '':
        filename = secure_filename(file.filename)
        img_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(img_path)
        img_path = img_path.replace("static/", "")

    split_expenses_col.update_one({"_id": ObjectId(split_id)}, {
        "$set": {
            "status": "paid",
            "payment_image": img_path,
            "paid_amount": amount_paid,
            "paid_at": datetime.now()
        }
    })

    flash("Payment marked successfully!", "success")
    return redirect(url_for("my_splits"))



@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    app.run(debug=True)
