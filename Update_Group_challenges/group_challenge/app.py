from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from bson import ObjectId
from twilio.rest import Client
from datetime import datetime
import os

# Flask & Config
app = Flask(__name__)
app.secret_key = "your_secret_key"
UPLOAD_FOLDER = "static/payments"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# MongoDB Setup
mongo_client = MongoClient("mongodb://localhost:27017/")
mongo_db = mongo_client["wealthyfyme"]
split_expenses_col = mongo_db["split_expenses"]
challenges_col = mongo_db['group_challenges']

# Twilio Config
TWILIO_ACCOUNT_SID = 'AC3dd54a862fc10fcf2284f9508cb56a24'
TWILIO_AUTH_TOKEN = '824084d4dd7119146c3e23841438'
TWILIO_PHONE_NUMBER = '+16814413892'
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


@app.route('/')
def group_challenges():
    user_email = session.get('email', 'ashish123@gmail.com')
    challenges = list(challenges_col.find({"emails": {"$in": [user_email]}}))
    for c in challenges:
        try:
            c['amount'] = float(c['amount'])
        except (KeyError, TypeError, ValueError):
            c['amount'] = 0.0
        try:
            c['progress'] = float(c.get('progress', 0))
        except (KeyError, TypeError, ValueError):
            c['progress'] = 0.0

    # Fetch splits user owes (role=participant, status!=paid) within these challenges
    challenge_ids = [c['_id'] for c in challenges]
    owed_splits = list(split_expenses_col.find({
        "email": user_email,
        "role": "participant",
        "status": {"$ne": "paid"},
        "challenge_id": {"$in": challenge_ids}
    }))

    return render_template("group_challenges.html", challenges=challenges, owed_splits=owed_splits)




@app.route('/challenge/<challenge_id>')
def view_challenge(challenge_id):
    challenge = challenges_col.find_one({"_id": ObjectId(challenge_id)})
    if not challenge:
        flash("Challenge not found", "danger")
        return redirect(url_for('group_challenges'))
    return render_template("challenge_details.html", challenge=challenge)


@app.route("/add_challenge", methods=["POST"])
def add_challenge():
    data = request.get_json()
    data["progress"] = 0
    challenges_col.insert_one(data)
    return jsonify({"status": "success"})



@app.route("/split_expense", methods=["GET", "POST"])
def split_expense():
    user_email = session.get("email", "ashish123@gmail.com")
    challenges = list(challenges_col.find({"emails": {"$in": [user_email]}}))

    if request.method == "POST":
        payer = request.form["payer_email"].strip()
        participants = [email.strip() for email in request.form["participant_emails"].split(",") if email.strip()]
        amount = float(request.form["amount"])
        challenge_id = request.form.get("challenge_id")

        if not participants or amount <= 0:
            flash("Invalid input.")
            return redirect(url_for("split_expense"))

        split_amount = round(amount / (len(participants) + 1), 2)
        timestamp = datetime.now()

        expense_data = {
            "email": payer,
            "role": "payer",
            "participants": participants,
            "amount": amount,
            "split_amount": split_amount,
            "timestamp": timestamp
        }

        if challenge_id:
            expense_data["challenge_id"] = ObjectId(challenge_id)

        split_expenses_col.insert_one(expense_data)

        for email in participants:
            participant_data = {
                "email": email,
                "role": "participant",
                "payer": payer,
                "amount": amount,
                "split_amount": split_amount,
                "status": "unpaid",
                "timestamp": timestamp
            }

            if challenge_id:
                participant_data["challenge_id"] = ObjectId(challenge_id)

            split_expenses_col.insert_one(participant_data)

            try:
                twilio_client.messages.create(
                    body=f"You owe ₹{split_amount} to {payer}.",
                    from_=TWILIO_PHONE_NUMBER,
                    to="+917876134701"  # Replace with dynamic phone number
                )
            except Exception as e:
                print(f"SMS failed for {email}: {e}")

        flash("Expense split successfully!")
        return redirect(url_for("split_expense"))

    return render_template("split_expense.html", challenges=challenges)


@app.route("/my_splits")
def my_splits():
    user_email = session.get("email", "ashish123@gmail.com")
    filter_type = request.args.get("type")
    query = {"email": user_email}

    if filter_type == "owe":
        query["role"] = "participant"
        query["status"] = {"$ne": "paid"}
    elif filter_type == "paid":
        query["$or"] = [
            {"email": user_email, "role": "participant", "status": "paid"},
            {"email": user_email, "role": "payer"}
        ]

    my_records = list(split_expenses_col.find(query).sort("timestamp", -1))

    # Monthly summary
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

    # Update challenge progress if applicable
    if record.get("challenge_id"):
        challenges_col.update_one(
            {"_id": record["challenge_id"]},
            {"$inc": {"progress": amount_paid}}
        )

        challenge = challenges_col.find_one({"_id": record["challenge_id"]})
        if challenge and challenge["progress"] >= challenge["amount"]:
            for email in challenge["emails"]:
                try:
                    twilio_client.messages.create(
                        body=f"🎉 Challenge '{challenge['name']}' completed!",
                        from_=TWILIO_PHONE_NUMBER,
                        to="+917876134701"  # Replace with user's phone
                    )
                except Exception as e:
                    print(f"SMS failed for {email}: {e}")

    flash("Payment marked successfully!", "success")
    return redirect(url_for("my_splits"))


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__ == '__main__':
    app.run(debug=True)
