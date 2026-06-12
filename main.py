from flask import Flask, request, jsonify
import anthropic
import os
import traceback
import requests
import json
import time
import threading
import random

app = Flask(__name__)

QUO_API_KEY = os.environ.get("QUO_API_KEY")
QUO_PHONE_NUMBER = os.environ.get("QUO_PHONE_NUMBER")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
OWNER_NUMBER = os.environ.get("OWNER_NUMBER")

STATE_FILE = "/tmp/wrapmotive_state.json"

LUXURY_BRANDS = ["mercedes", "bmw", "audi", "porsche", "ferrari", "lamborghini",
                 "maserati", "bentley", "rolls royce", "rolls-royce", "aston martin",
                 "tesla", "lexus", "cadillac", "lincoln", "genesis", "infiniti", "acura"]

HIGH_TICKET_SERVICES = ["wrap", "ppf", "paint protection", "chrome delete", "ceramic coat",
                        "detailing", "detail", "body kit", "full wrap", "partial wrap"]


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"pending_approvals": {}, "human_active": {}}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print("State save error: " + str(e))


def is_premium_vehicle(year, make):
    try:
        vehicle_year = int(year)
    except Exception:
        vehicle_year = 0
    is_luxury = any(brand in make.lower() for brand in LUXURY_BRANDS)
    return vehicle_year >= 2019 or is_luxury


def is_high_ticket(service):
    service_lower = service.lower()
    return any(keyword in service_lower for keyword in HIGH_TICKET_SERVICES)


def send_sms(to_number, message):
    url = "https://api.openphone.com/v1/messages"
    headers = {
        "Authorization": QUO_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "content": message,
        "from": QUO_PHONE_NUMBER,
        "to": [to_number]
    }
    response = requests.post(url, json=payload, headers=headers)
    print("Quo SMS response: " + str(response.status_code) + " " + str(response.text))
    return response


def send_delayed_sms(to_number, message, delay_seconds):
    time.sleep(delay_seconds)
    state = load_state()
    if state["human_active"].get(to_number):
        print("Human active for " + to_number + " - AI skipping send")
        return
    send_sms(to_number, message)


def parse_form_data(data):
    try:
        raw = json.loads(data.get("rawRequest", "{}"))
    except Exception:
        raw = {}

    name = raw.get("q2_q2_fullname0", data.get("q2_q2_fullname0", {}))
    if isinstance(name, dict):
        name = (name.get("first", "") + " " + name.get("last", "")).strip()

    phone = raw.get("q3_q3_phone1", data.get("q3_q3_phone1", {}))
    if isinstance(phone, dict):
        phone = phone.get("full", "")

    email = raw.get("q4_q4_email2", data.get("q4_q4_email2", ""))
    vehicle = raw.get("q5_q5_textbox3", data.get("q5_q5_textbox3", ""))

    service = raw.get("q9_servicesNeeded", data.get("q9_servicesNeeded", ""))
    if isinstance(service, list):
        service = ", ".join(service)

    details = raw.get("q10_message", data.get("q10_message", ""))

    return name, phone, email, vehicle, service, details


def get_ai_response(customer_data, is_premium, service):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    high_ticket = is_high_ticket(service)

    system_prompt = """You are Shawn from WrapMotive, a premium auto wrap and tint shop in the DMV area.
You text customers directly as Shawn. Knowledgeable, genuine, passionate about cars. Never use emojis. Never say you are an AI.

TINT PRICING - YOU MAY QUOTE THESE:
CARBON TINT:
- Coupe/Sedan sides + back: $230 (minimum $220)
- Coupe/Sedan front windshield: $110, per window $65 (min $60)
- Mid-size SUV sides + back: $280
- Large SUV (Escalade etc) sides + back: $310
- SUV front windshield: $130 (min $120), per window $65

CERAMIC TINT:
- Coupe/Sedan sides + back: $399 (normally $499, on special now)
- Coupe/Sedan front windshield: $150, per window $80
- Small/Mid SUV (RAV4 size) sides + back: $450, front windshield $150
- Large SUV sides + back: $499, front windshield $180

TINT SALES RULES:
- Premium vehicle (2019+ or luxury brand): Ask carbon or ceramic first, explain difference. Ceramic = better heat rejection, clearer visibility, lasts longer.
- Older or standard vehicle: Give both prices straight up, let them choose.
- Never oversell. Accept every job.
- Naturally mention ceramic coating add-on when it fits.

HIGH TICKET RULES (wraps, PPF, chrome delete, ceramic coating, detailing, body kits):
- NEVER give pricing. Tell them you will put together a custom quote.
- Ask about their vision, current color, timeline. Get them excited.
- Be genuinely passionate. These are the jobs you love most.

VOICE RULES:
- Greet with first name only: Hey [first name], its Shawn from WrapMotive!
- No emojis ever.
- Warm, genuine, passionate. You love cars and what you do.
- Short natural texts like a real person. Not a wall of text.
- Reference the actual vehicle specifically, not generic compliments.
- You can say things like that is going to look insane or perfect choice when it genuinely fits."""

    if high_ticket:
        user_message = (
            "HIGH TICKET lead - do NOT quote pricing, warm them up and ask about their vision.\n"
            "Name: " + str(customer_data.get("name")) + "\n"
            "Vehicle: " + str(customer_data.get("vehicle")) + "\n"
            "Service: " + str(service) + "\n"
            "Details: " + str(customer_data.get("details", "None")) + "\n"
            "Premium vehicle: " + str(is_premium) + "\n\n"
            "Write the first text to send this customer. Warm, excited, ask about their vision."
        )
    else:
        user_message = (
            "TINT lead - you may quote pricing.\n"
            "Name: " + str(customer_data.get("name")) + "\n"
            "Vehicle: " + str(customer_data.get("vehicle")) + "\n"
            "Service: " + str(service) + "\n"
            "Details: " + str(customer_data.get("details", "None")) + "\n"
            "Premium vehicle: " + str(is_premium) + "\n\n"
            "Write the first text to send this customer."
        )

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    return message.content[0].text


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.form.to_dict() if request.form else request.json or {}
        print("Received lead: " + str(data))

        name, phone, email, vehicle, service, details = parse_form_data(data)
        print("Parsed - Name: " + str(name) + " Phone: " + str(phone) + " Vehicle: " + str(vehicle) + " Service: " + str(service))

        vehicle_parts = vehicle.split(" ") if vehicle else []
        year = vehicle_parts[0] if vehicle_parts else "0"
        make = vehicle_parts[1] if len(vehicle_parts) > 1 else ""

        premium = is_premium_vehicle(year, make)
        high_ticket = is_high_ticket(service)

        customer_data = {
            "name": name,
            "phone": phone,
            "email": email,
            "vehicle": vehicle,
            "details": details
        }

        ai_message = get_ai_response(customer_data, premium, service)
        print("AI drafted: " + ai_message)

        clean_phone = "".join(filter(str.isdigit, str(phone)))
        if len(clean_phone) == 10:
            clean_phone = "+1" + clean_phone
        elif len(clean_phone) == 11:
            clean_phone = "+" + clean_phone

        state = load_state()
        state["pending_approvals"][clean_phone] = {
            "customer_name": name,
            "customer_phone": clean_phone,
            "vehicle": vehicle,
            "service": service,
            "message": ai_message,
            "high_ticket": high_ticket,
            "timestamp": time.time()
        }
        save_state(state)

        premium_label = "YES" if premium else "No"
        high_ticket_label = "HIGH TICKET - YOU CLOSE" if high_ticket else "Tint - AI Quoted"

        approval_msg = (
            "NEW LEAD - APPROVAL NEEDED\n"
            "Name: " + str(name) + "\n"
            "Phone: " + str(clean_phone) + "\n"
            "Vehicle: " + str(vehicle) + "\n"
            "Service: " + str(service) + "\n"
            "Type: " + high_ticket_label + "\n"
            "Premium: " + premium_label + "\n\n"
            "PROPOSED MESSAGE:\n" + ai_message + "\n\n"
            "Reply Y to send, or reply with corrections."
        )

        send_sms(OWNER_NUMBER, approval_msg)
        return jsonify({"status": "success"}), 200

    except Exception as e:
        error_details = traceback.format_exc()
        print("FULL ERROR: " + error_details)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/approve", methods=["POST"])
def approve():
    try:
        data = request.json or {}
        print("Approve webhook received: " + str(data))

        body = data.get("body", {})
        if not body:
            body = data

        from_number = body.get("from", "")
        message_text = str(body.get("text", "")).strip()

        clean_from = "".join(filter(str.isdigit, str(from_number)))
        if len(clean_from) == 10:
            clean_from = "+1" + clean_from
        elif len(clean_from) == 11:
            clean_from = "+" + clean_from

        clean_owner = "".join(filter(str.isdigit, str(OWNER_NUMBER)))
        if len(clean_owner) == 10:
            clean_owner = "+1" + clean_owner
        elif len(clean_owner) == 11:
            clean_owner = "+" + clean_owner

        if clean_from != clean_owner:
            print("Not from owner - from: " + clean_from + " owner: " + clean_owner)
            return jsonify({"status": "not owner"}), 200

        state = load_state()

        if message_text.upper() == "Y":
            if not state["pending_approvals"]:
                send_sms(OWNER_NUMBER, "No pending approvals found.")
                return jsonify({"status": "no pending"}), 200

            latest_phone = max(
                state["pending_approvals"],
                key=lambda k: state["pending_approvals"][k]["timestamp"]
            )
            pending = state["pending_approvals"][latest_phone]
            delay = random.randint(45, 90)

            thread = threading.Thread(
                target=send_delayed_sms,
                args=(pending["customer_phone"], pending["message"], delay)
            )
            thread.daemon = True
            thread.start()

            del state["pending_approvals"][latest_phone]
            save_state(state)

            send_sms(OWNER_NUMBER, "Sending to " + str(pending["customer_name"]) + " in ~" + str(delay) + " seconds.")
            return jsonify({"status": "approved"}), 200

        lines = message_text.split("\n")
        if len(lines) >= 2:
            target_phone = lines[0].strip()
            corrected_message = "\n".join(lines[1:]).strip()

            clean_target = "".join(filter(str.isdigit, target_phone))
            if len(clean_target) == 10:
                clean_target = "+1" + clean_target
            elif len(clean_target) == 11:
                clean_target = "+" + clean_target

            if clean_target in state["pending_approvals"]:
                pending = state["pending_approvals"][clean_target]
                delay = random.randint(45, 90)

                thread = threading.Thread(
                    target=send_delayed_sms,
                    args=(clean_target, corrected_message, delay)
                )
                thread.daemon = True
                thread.start()

                del state["pending_approvals"][clean_target]
                save_state(state)

                send_sms(OWNER_NUMBER, "Corrected message sending to " + str(pending["customer_name"]) + " in ~" + str(delay) + " seconds.")
                return jsonify({"status": "corrected and sent"}), 200

        return jsonify({"status": "unrecognized"}), 200

    except Exception as e:
        print("FULL ERROR in approve: " + traceback.format_exc())
        return jsonify({"status": "error"}), 500


@app.route("/human-reply", methods=["POST"])
def human_reply():
    try:
        data = request.json or {}
        print("Human reply webhook: " + str(data))

        event_type = data.get("type", "")
        body = data.get("body", {})
        if not body:
            body = data

        if "delivered" not in str(event_type) and "sent" not in str(event_type):
            return jsonify({"status": "not a sent message"}), 200

        user_id = body.get("userId", None)
        if not user_id:
            print("No userId - sent by AI, ignoring")
            return jsonify({"status": "ai message ignored"}), 200

        to_number = body.get("to", "")
        if isinstance(to_number, list):
            to_number = to_number[0] if to_number else ""

        clean_to = "".join(filter(str.isdigit, str(to_number)))
        if len(clean_to) == 10:
            clean_to = "+1" + clean_to
        elif len(clean_to) == 11:
            clean_to = "+" + clean_to

        state = load_state()
        state["human_active"][clean_to] = True

        if clean_to in state["pending_approvals"]:
            del state["pending_approvals"][clean_to]

        save_state(state)
        print("Human active flagged for: " + clean_to)
        return jsonify({"status": "flagged"}), 200

    except Exception as e:
        print("FULL ERROR in human_reply: " + traceback.format_exc())
        return jsonify({"status": "error"}), 500


@app.route("/", methods=["GET"])
def home():
    return "WrapMotive AI is running.", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
