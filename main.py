from flask import Flask, request, jsonify
from twilio.rest import Client
import anthropic
import os
import traceback

app = Flask(__name__)

TWILIO_SID = os.environ.get("TWILIO_SID")
TWILIO_TOKEN = os.environ.get("TWILIO_TOKEN")
TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
TEAM_NUMBERS = os.environ.get("TEAM_NUMBERS", "").split(",")

LUXURY_BRANDS = ["mercedes", "bmw", "audi", "porsche", "ferrari", "lamborghini",
                 "maserati", "bentley", "rolls royce", "rolls-royce", "aston martin",
                 "tesla", "lexus", "cadillac", "lincoln", "genesis", "infiniti", "acura"]


def is_premium_vehicle(year, make):
    try:
        vehicle_year = int(year)
    except Exception:
        vehicle_year = 0
    is_luxury = any(brand in make.lower() for brand in LUXURY_BRANDS)
    return vehicle_year >= 2019 or is_luxury


def send_sms(to_number, message):
    client = Client(TWILIO_SID, TWILIO_TOKEN)
    client.messages.create(body=message, from_=TWILIO_NUMBER, to=to_number)


def notify_team(message):
    for number in TEAM_NUMBERS:
        number = number.strip()
        if number:
            send_sms(number, message)


def get_ai_response(customer_data, is_premium, service):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    system_prompt = """You are Shawn from WrapMotive, a premium auto wrap and tint shop.
You text customers directly as Shawn. You are knowledgeable, casual but professional.
Never say you are an AI. Sign off as Shawn from WrapMotive.

PRICING KNOWLEDGE:
CARBON TINT:
- Coupe/Sedan sides + back: start at $230 (minimum $220)
- Coupe/Sedan front windshield: $110, per window $65 (minimum $60)
- Mid-size SUV sides + back: $280
- Large SUV (Escalade etc) sides + back: $310
- SUV front windshield: start $130 (minimum $120), per window $65 minimum

CERAMIC TINT:
- Coupe/Sedan sides + back: $399 (normally $499, currently on special)
- Coupe/Sedan front windshield: $150, per window $80 minimum
- Small/Mid SUV (RAV4 etc) sides + back: $450, front windshield $150
- Large SUV sides + back: $499, front windshield $180

SALES RULES:
- For premium vehicles (2019+ or luxury brand): Always ask if they want carbon or ceramic, explain the difference if needed. Ceramic is higher quality, better heat rejection, clearer vision, lasts longer.
- For older or standard vehicles: Give pricing for both carbon and ceramic straight up, let them choose.
- NEVER oversell.
- For wraps, PPF, chrome delete, ceramic coating, detailing, body kits: Warm up the lead, ask questions about their vision, get them excited. DO NOT give final pricing - tell them you will put together a custom quote.
- Always greet with: Hey [Name], its Shawn from WrapMotive!
- Keep texts conversational, not too long, like a real person texting."""

    user_message = (
        "New customer inquiry:\n"
        "Name: " + str(customer_data.get("name")) + "\n"
        "Vehicle: " + str(customer_data.get("vehicle")) + "\n"
        "Service: " + str(service) + "\n"
        "Details: " + str(customer_data.get("details", "None provided")) + "\n"
        "Premium vehicle: " + str(is_premium) + "\n\n"
        "Write the first text message to send this customer."
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    return message.content[0].text


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.form.to_dict() if request.form else request.json or {}
        print("Received data: " + str(data))

        name = data.get("q3_fullName", {})
        if isinstance(name, dict):
            name = (name.get("first", "") + " " + name.get("last", "")).strip()

        phone = data.get("q4_phoneNumber", {})
        if isinstance(phone, dict):
            phone = phone.get("full", "")

        email = data.get("q5_email", "")
        vehicle = data.get("q6_vehicleYear", "")
        service = data.get("q7_servicesNeeded", "")
        if isinstance(service, list):
            service = ", ".join(service)
        details = data.get("q8_projectDetails", "")

        vehicle_parts = vehicle.split(" ") if vehicle else []
        year = vehicle_parts[0] if vehicle_parts else "0"
        make = vehicle_parts[1] if len(vehicle_parts) > 1 else ""

        premium = is_premium_vehicle(year, make)

        customer_data = {
            "name": name,
            "phone": phone,
            "email": email,
            "vehicle": vehicle,
            "details": details
        }

        ai_message = get_ai_response(customer_data, premium, service)
        print("AI response: " + ai_message)

        clean_phone = "".join(filter(str.isdigit, str(phone)))
        if len(clean_phone) == 10:
            clean_phone = "+1" + clean_phone
        elif len(clean_phone) == 11:
            clean_phone = "+" + clean_phone

        send_sms(clean_phone, ai_message)

        premium_label = "YES" if premium else "No"
        team_msg = (
            "NEW WRAPMOTIVE LEAD\n"
            "Name: " + str(name) + "\n"
            "Phone: " + str(phone) + "\n"
            "Vehicle: " + str(vehicle) + "\n"
            "Service: " + str(service) + "\n"
            "Details: " + str(details) + "\n"
            "Premium Vehicle: " + premium_label
        )
        notify_team(team_msg)

        return jsonify({"status": "success"}), 200

    except Exception as e:
        error_details = traceback.format_exc()
        print("FULL ERROR: " + error_details)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/", methods=["GET"])
def home():
    return "WrapMotive AI is running.", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
