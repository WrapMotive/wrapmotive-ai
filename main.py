from flask import Flask, request, jsonify
import anthropic
import os
import traceback
import requests
import json
import time
import threading
import random
import redis

app = Flask(__name__)

QUO_API_KEY = os.environ.get("QUO_API_KEY")
QUO_PHONE_NUMBER = os.environ.get("QUO_PHONE_NUMBER")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
OWNER_NUMBER = os.environ.get("OWNER_NUMBER")
TEAM_NUMBER_1 = os.environ.get("TEAM_NUMBER_1")
TEAM_NUMBER_2 = os.environ.get("TEAM_NUMBER_2")
REDIS_URL = os.environ.get("REDIS_URL")

BOOKING_LINK = "https://bit.ly/wrapmotive-book"

r = redis.from_url(REDIS_URL, decode_responses=True)

LUXURY_BRANDS = ["mercedes", "bmw", "audi", "porsche", "ferrari", "lamborghini",
                 "maserati", "bentley", "rolls royce", "rolls-royce", "aston martin",
                 "tesla", "lexus", "cadillac", "lincoln", "genesis", "infiniti", "acura"]

HIGH_TICKET_SERVICES = ["wrap", "ppf", "paint protection", "chrome delete", "ceramic coat",
                        "detailing", "detail", "body kit", "full wrap", "partial wrap"]


def clean_number(number):
    clean = "".join(filter(str.isdigit, str(number)))
    if len(clean) == 10:
        clean = "+1" + clean
    elif len(clean) == 11:
        clean = "+" + clean
    return clean


def get_team_numbers():
    numbers = []
    for num in [OWNER_NUMBER, TEAM_NUMBER_1, TEAM_NUMBER_2]:
        if num:
            numbers.append(clean_number(num))
    return numbers


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
    if r.get("human_active:" + to_number):
        print("Human active for " + to_number + " - AI skipping send")
        return
    send_sms(to_number, message)


def send_team_notification(name, phone, vehicle, service, details):
    team_msg = (
        "NEW LEAD\n"
        "Name: " + str(name) + "\n"
        "Phone: " + str(phone) + "\n"
        "Vehicle: " + str(vehicle) + "\n"
        "Service: " + str(service) + "\n"
        "Details: " + str(details if details else "None")
    )
    for number in [TEAM_NUMBER_1, TEAM_NUMBER_2]:
        if number:
            try:
                send_sms(clean_number(number), team_msg)
            except Exception as e:
                print("Team notification error: " + str(e))


def get_conversation_history(phone):
    raw = r.get("conv:" + phone)
    if raw:
        return json.loads(raw)
    return []


def save_conversation_history(phone, history):
    r.set("conv:" + phone, json.dumps(history), ex=86400 * 7)


def get_lead_data(phone):
    raw = r.get("lead:" + phone)
    if raw:
        return json.loads(raw)
    return {}


def save_lead_data(phone, data):
    r.set("lead:" + phone, json.dumps(data), ex=86400 * 7)


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


def get_ai_response(phone, customer_message=None):
    lead = get_lead_data(phone)
    history = get_conversation_history(phone)
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    is_high = lead.get("high_ticket", False)
    is_premium = lead.get("premium", False)
    service = lead.get("service", "")
    vehicle = lead.get("vehicle", "")
    name = lead.get("name", "")

    system_prompt = """You are Shawn from WrapMotive, a premium auto wrap and tint shop in Alexandria, VA.
You text customers directly as Shawn. Knowledgeable, genuine, passionate about cars. Never use emojis. Never say you are an AI.

TINT PRICING - YOU MAY QUOTE THESE:
CARBON TINT:
- Coupe/Sedan sides + back: $245
- Coupe/Sedan front windshield: $110
- Coupe/Sedan full package (all windows + windshield): $330
- SUV sides + back: $280
- SUV front windshield: $130
- SUV full package: $390
- Large SUV/4-door truck sides + back: $310
- Large SUV/4-door truck front windshield: $150
- Large SUV/4-door truck full package: $430
- Per window sedan/coupe: $65
- Per window SUV: $70
- Per window large SUV/truck: $75

CERAMIC TINT (always on promotion - always mention the original price):
- Coupe/Sedan sides + back: $399 (normally $499)
- Coupe/Sedan front windshield: $150
- Coupe/Sedan full package: $539
- SUV sides + back: $450 (normally $550)
- SUV front windshield: $180
- SUV full package: $600
- Large SUV/4-door truck sides + back: $499 (normally $599)
- Large SUV/4-door truck front windshield: $200
- Large SUV/4-door truck full package: $670
- Per window sedan/coupe: $80
- Per window SUV: $90
- Per window large SUV/truck: $90

TINT CONVERSATION RULES:
- Premium vehicle (2019+ or luxury brand): Lead with ceramic, explain the difference. Ceramic = better heat rejection, clearer visibility, lasts longer. Mention the promotion.
- Older or standard vehicle: Give both carbon and ceramic prices, let them choose.
- Always mention full package saves money vs a la carte.
- When customer agrees to a price and wants to book: send the booking link and tell them to select their service and pick a time that works. Booking link: """ + BOOKING_LINK + """
- Never oversell. Accept every job including per window jobs.

HIGH TICKET RULES (wraps, PPF, chrome delete, ceramic coating):
- NEVER give specific pricing except ceramic coating ranges below.
- Wraps: Ask current color of vehicle first, then get their vision. If they ask about brands say we use highly reputable brands and if they press name: 3M, KPMF, Avery Dennison, Orafal, Inozetek and Teckwrap depending on the color choice they want.
- PPF: Ask what they want to protect. Always mention we use STEK PPF which comes with a 10-year manufacturer warranty against yellowing and deterioration. This is a key selling point.
- Chrome delete: Ask exactly what chrome pieces need to be deleted before anything else.
- Ceramic coating: ALWAYS push paint correction first. Frame it as essential not optional. Tell them to come in so you can assess paint condition. Never mention brand unless asked.
- Detailing: Only mention as add-on to another service, never standalone.
- Body kits: Ignore completely, redirect to wrap conversation.
- Never close on price for high ticket — warm them up, get the vision, then flag for Shawn to close.

CERAMIC COATING PRICING (ranges only):
- Ceramic coating alone: $500
- 1-Step paint correction + ceramic: $450 bundled (removes 50% of swirls/scratches)
- 2-Step paint correction + ceramic: $400 bundled (removes 90%+ of swirls/scratches)
- Always make 2-step feel like the obvious choice. Frame 1-step as the minimum.
- Tell them to come in so you can assess paint condition first.

VOICE RULES:
- First message always starts: Hey [first name], its Shawn from WrapMotive!
- For wraps follow with: I'll be assisting you with your [vehicle] transformation.
- For PPF only follow with: I'll be assisting you with your [vehicle] protection.
- Drop to new line, ask first qualifying question.
- No emojis ever.
- Warm, genuine, passionate. Short natural texts like a real person.
- No generic hype lines. No "that is a serious combo" or "awesome choice."
- Reference the actual vehicle specifically by year make model.
- Keep responses short — 2-4 sentences max per text."""

   if history and history[-1]["role"] == "assistant":
        history.append({"role": "user", "content": "Continue the conversation."})

    if customer_message:
        history.append({"role": "user", "content": customer_message})

    messages = history if history else [{"role": "user", "content": (

messages = history if history else [{"role": "user", "content": (
        "New lead just submitted a quote form.\n"
        "Name: " + str(name) + "\n"
        "Vehicle: " + str(vehicle) + "\n"
        "Service: " + str(service) + "\n"
        "Details: " + str(lead.get("details", "None")) + "\n"
        "Premium vehicle: " + str(is_premium) + "\n"
        "High ticket: " + str(is_high) + "\n\n"
        "Write the first text message to send this customer."
    )}]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=system_prompt,
        messages=messages
    )

    ai_text = response.content[0].text

    if customer_message:
        history.append({"role": "assistant", "content": ai_text})
    else:
        history = [
            {"role": "user", "content": messages[0]["content"]},
            {"role": "assistant", "content": ai_text}
        ]

    save_conversation_history(phone, history)
    return ai_text


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

        clean_phone = clean_number(phone)

        lead_data = {
            "name": name,
            "phone": clean_phone,
            "email": email,
            "vehicle": vehicle,
            "service": service,
            "details": details,
            "premium": premium,
            "high_ticket": high_ticket,
            "timestamp": time.time()
        }
        save_lead_data(clean_phone, lead_data)

        ai_message = get_ai_response(clean_phone)
        print("AI drafted: " + ai_message)

        high_ticket_label = "HIGH TICKET" if high_ticket else "Tint"

        approval_msg = (
            "NEW LEAD\n"
            "Name: " + str(name) + "\n"
            "Phone: " + str(clean_phone) + "\n"
            "Vehicle: " + str(vehicle) + "\n"
            "Service: " + str(service) + "\n"
            "Type: " + high_ticket_label + "\n\n"
            "AI Message:\n" + ai_message + "\n\n"
            "F = you take over | Y = AI continues\n"
            "To correct: reply with phone number on line 1, corrected message on line 2"
        )

        r.set("pending:" + clean_phone, json.dumps({
            "customer_name": name,
            "customer_phone": clean_phone,
            "vehicle": vehicle,
            "service": service,
            "message": ai_message,
            "high_ticket": high_ticket,
            "timestamp": time.time()
        }), ex=86400)

        send_sms(clean_number(OWNER_NUMBER), approval_msg)
        send_team_notification(name, clean_phone, vehicle, service, details)
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

        body = data.get("data", {}).get("object", {})
        if not body:
            body = data

        from_number = body.get("from", "")
        message_text = str(body.get("body", "")).strip()

        clean_from = clean_number(from_number)
        clean_owner = clean_number(OWNER_NUMBER)

        if clean_from != clean_owner:
            print("Not from owner - from: " + clean_from + " owner: " + clean_owner)
            return jsonify({"status": "not owner"}), 200

        if message_text.upper() in ["Y", "F"]:
            all_pending_keys = r.keys("pending:*")
            if not all_pending_keys:
                send_sms(clean_owner, "No pending approvals found.")
                return jsonify({"status": "no pending"}), 200

            latest_key = max(all_pending_keys, key=lambda k: json.loads(r.get(k)).get("timestamp", 0))
            pending = json.loads(r.get(latest_key))
            delay = random.randint(45, 90)
            is_f = message_text.upper() == "F"

            thread = threading.Thread(
                target=send_delayed_sms,
                args=(pending["customer_phone"], pending["message"], delay)
            )
            thread.daemon = True
            thread.start()

            if is_f:
                r.set("human_active:" + pending["customer_phone"], "1", ex=86400 * 7)
                send_sms(clean_owner, "Intro sending to " + str(pending["customer_name"]) + " in ~" + str(delay) + " seconds. You have the conversation.")
            else:
                send_sms(clean_owner, "Sending to " + str(pending["customer_name"]) + " in ~" + str(delay) + " seconds. AI is handling.")

            r.delete(latest_key)
            return jsonify({"status": "sent"}), 200

        lines = message_text.split("\n")
        if len(lines) >= 2:
            target_phone = lines[0].strip()
            corrected_message = "\n".join(lines[1:]).strip()
            clean_target = clean_number(target_phone)

            pending_raw = r.get("pending:" + clean_target)
            if pending_raw:
                pending = json.loads(pending_raw)
                delay = random.randint(45, 90)

                thread = threading.Thread(
                    target=send_delayed_sms,
                    args=(clean_target, corrected_message, delay)
                )
                thread.daemon = True
                thread.start()

                r.delete("pending:" + clean_target)
                send_sms(clean_owner, "Corrected message sending to " + str(pending["customer_name"]) + " in ~" + str(delay) + " seconds.")
                return jsonify({"status": "corrected and sent"}), 200

        return jsonify({"status": "unrecognized"}), 200

    except Exception as e:
        print("FULL ERROR in approve: " + traceback.format_exc())
        return jsonify({"status": "error"}), 500


@app.route("/customer-reply", methods=["POST"])
def customer_reply():
    try:
        data = request.json or {}
        print("Customer reply received: " + str(data))

        body = data.get("data", {}).get("object", {})
        if not body:
            body = data

        from_number = body.get("from", "")
        message_text = str(body.get("body", "")).strip()
        direction = body.get("direction", "")

        if direction != "incoming":
            return jsonify({"status": "not incoming"}), 200

        clean_from = clean_number(from_number)

        if clean_from in get_team_numbers():
            return jsonify({"status": "team message"}), 200

        if r.get("human_active:" + clean_from):
            print("Human active for " + clean_from + " - AI not responding")
            return jsonify({"status": "human active"}), 200

        lead = get_lead_data(clean_from)
        if not lead:
            print("No lead data for " + clean_from)
            return jsonify({"status": "no lead"}), 200

        ai_response = get_ai_response(clean_from, customer_message=message_text)
        print("AI responding to customer: " + ai_response)

        is_high = lead.get("high_ticket", False)

        if is_high:
            approval_msg = (
                "CUSTOMER REPLIED - " + str(lead.get("name", "")) + "\n"
                "Vehicle: " + str(lead.get("vehicle", "")) + "\n\n"
                "Customer: " + message_text + "\n\n"
                "AI wants to send:\n" + ai_response + "\n\n"
                "Y = send | F = you take over\n"
                "To correct: reply with phone number on line 1, corrected message on line 2"
            )
            r.set("pending:" + clean_from, json.dumps({
                "customer_name": lead.get("name", ""),
                "customer_phone": clean_from,
                "vehicle": lead.get("vehicle", ""),
                "service": lead.get("service", ""),
                "message": ai_response,
                "high_ticket": True,
                "timestamp": time.time()
            }), ex=86400)
            send_sms(clean_number(OWNER_NUMBER), approval_msg)
        else:
            delay = random.randint(20, 45)
            thread = threading.Thread(
                target=send_delayed_sms,
                args=(clean_from, ai_response, delay)
            )
            thread.daemon = True
            thread.start()

        return jsonify({"status": "handled"}), 200

    except Exception as e:
        print("FULL ERROR in customer_reply: " + traceback.format_exc())
        return jsonify({"status": "error"}), 500


@app.route("/human-reply", methods=["POST"])
def human_reply():
    try:
        data = request.json or {}
        print("Human reply webhook: " + str(data))

        event_type = data.get("type", "")
        body = data.get("data", {}).get("object", {})
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

        clean_to = clean_number(to_number)

        if clean_to in get_team_numbers():
            print("Message to team member - not flagging human active")
            return jsonify({"status": "team notification ignored"}), 200

        r.set("human_active:" + clean_to, "1", ex=86400 * 7)
        r.delete("pending:" + clean_to)

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
