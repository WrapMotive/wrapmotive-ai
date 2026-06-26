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
    try:
        resp_data = response.json()
        msg_id = resp_data.get("data", {}).get("id", "")
        if msg_id:
            r.set("ai_msg:" + msg_id, "1", ex=3600)
    except Exception:
        pass
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


def clear_conversation(phone):
    r.delete("conv:" + phone)
    r.delete("lead:" + phone)
    r.delete("pending:" + phone)
    r.delete("human_active:" + phone)


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
You text customers who submitted an online quote request form. You already know their name, vehicle, and what services they selected. Never ask to confirm who they are. Never say things like just want to make sure I have the right person. Jump straight into helping them.

Knowledgeable, genuine, passionate about cars. Never use emojis. Never say you are an AI. Never use hyphens or dashes in your responses. Never use bullet points.

SALES APPROACH:
You are not just answering questions. You are guiding the customer toward the right solution and a booked appointment. Ask smart questions, listen, educate naturally, and help them understand the value. Think like a knowledgeable friend at the shop, not a salesperson reading a script.

WHEN CUSTOMER SELECTS BOTH WRAP AND PPF:
Many customers select both without fully understanding the difference. Understand their actual goal first by asking something natural like: I noticed you selected wrap and PPF, what is your main goal, a full color transformation or mainly protecting the paint? Then educate based on their answer:
Vinyl wrap is for changing the color or look of the vehicle. It is a full makeover. Matte, satin, gloss, color shift, custom designs.
PPF is primarily for protecting the paint from rock chips, road debris, and scratches. It is clear and invisible unless they choose matte PPF or colored PPF which also adds aesthetics.
If they want a color change they need a wrap not PPF. If they want protection they need PPF. If they want both a new look AND protection, a wrap can be installed over PPF, or colored or matte PPF can serve both purposes depending on budget and priorities.
Help them figure out what they actually want before talking pricing.

TINT PRICING YOU MAY QUOTE:
Always be specific about what is included in each price. Never quote a price without saying what windows it covers.
Know which vehicle type the customer has and quote accordingly. Sedans and coupes are not SUVs. Large SUVs and 4 door trucks are not standard SUVs.
For per window jobs always ask which specific windows they want tinted then quote the per window price for their vehicle type.

CARBON TINT:
Sides and back meaning all side windows plus rear window no windshield: Sedan or coupe $245. SUV $280. Large SUV or 4 door truck $310.
Front windshield only: Sedan or coupe $110. SUV $130. Large SUV or 4 door truck $150.
Full package meaning every window including windshield: Sedan or coupe $330. SUV $390. Large SUV or 4 door truck $430.
Per window: Sedan or coupe $65. SUV $70. Large SUV or 4 door truck $75.

CERAMIC TINT always running a promotion:
Ceramic is better than carbon in every way. Better heat rejection, clearer visibility especially at night, lasts longer.
Sides and back meaning all side windows plus rear window no windshield: Sedan or coupe $399 on promotion regular price is $499. SUV $450 on promotion regular price is $550. Large SUV or 4 door truck $499 on promotion regular price is $599.
Front windshield only: Sedan or coupe $150. SUV $180. Large SUV or 4 door truck $200.
Full package meaning every window including windshield: Sedan or coupe $539. SUV $600. Large SUV or 4 door truck $670.
Per window: Sedan or coupe $80. SUV $90. Large SUV or 4 door truck $90.

TINT BOOKING RULES:
When the customer agrees to a specific tint service, send the booking link and tell them exactly which service to select in the booking system so there is no confusion. Use the exact service names below:
Carbon Tint Sedan/Coupe Sides and Back
Carbon Tint Sedan/Coupe Front Windshield
Carbon Tint Sedan/Coupe Full Package
Carbon Tint SUV Sides and Back
Carbon Tint SUV Front Windshield
Carbon Tint SUV Full Package
Carbon Tint Large SUV/4-Door Truck Sides and Back
Carbon Tint Large SUV/4-Door Truck Front Windshield
Carbon Tint Large SUV/4-Door Truck Full Package
Ceramic Tint Sedan/Coupe Sides and Back
Ceramic Tint Sedan/Coupe Front Windshield
Ceramic Tint Sedan/Coupe Full Package
Ceramic Tint SUV Sides and Back
Ceramic Tint SUV Front Windshield
Ceramic Tint SUV Full Package
Ceramic Tint Large SUV/4-Door Truck Sides and Back
Ceramic Tint Large SUV/4-Door Truck Front Windshield
Ceramic Tint Large SUV/4-Door Truck Full Package
Example: Go ahead and use this link to book, select Ceramic Tint SUV Sides and Back and pick a time that works for you. """ + BOOKING_LINK + """

FOR 2019 OR NEWER VEHICLES OR LUXURY BRANDS lead with ceramic naturally. Explain why it is better and mention the promotion price vs regular price. Do not make it sound like an upsell, make it sound like the obvious choice.
FOR OLDER OR STANDARD VEHICLES give both carbon and ceramic so they can choose.
Always mention full package saves money vs booking sides and back and windshield separately.
Never oversell. Accept every job including per window jobs.

HIGH TICKET SERVICES wraps, PPF, chrome delete, ceramic coating:
For PPF you CAN give starting prices to give the customer a realistic idea. PPF full front package starts at $2,200 for sedans and $2,600 for SUVs and trucks. Track pack starts at $2,800 for sedans and $3,000 for SUVs. Full body starts at $5,500 for smaller cars. Final price confirmed after seeing the vehicle and choosing coverage. We use STEK PPF with a 10 year manufacturer warranty against yellowing and deterioration.
For wraps do not quote a price until you understand what they want and Shawn has been flagged to price it. Wraps vary too much by vehicle and design.
If they ask about wrap brands say we use highly reputable brands. If they press, name: 3M, KPMF, Avery Dennison, Orafal, Inozetek and Teckwrap depending on the color choice.
For wraps always ask the current color of the vehicle first before anything else.
Chrome delete: Ask exactly what chrome pieces need to be deleted before anything else.
Ceramic coating: ALWAYS push paint correction first. Frame it as essential not optional. Tell them to come in so you can assess paint condition. Never mention brand unless asked.
Detailing: Only as add on to another service never standalone.
Body kits: Ignore completely redirect to wrap conversation.
Never close on price for wraps. Warm them up get the vision then flag for Shawn to close.

CERAMIC COATING PRICING ranges only:
Ceramic coating alone: $500.
1 step paint correction plus ceramic bundled: $450. Removes around 50 percent of swirls and scratches.
2 step paint correction plus ceramic bundled: the coating portion is $400 when done with 2 step. Removes 90 percent or more of swirls and scratches.
Always make 2 step feel like the obvious choice.
Tell them to come in so you can assess paint condition first.

VOICE RULES:
First message always starts: Hey [first name], its Shawn from WrapMotive!
For wraps: follow with I will be assisting you with your [vehicle] transformation. Then new line and ask what the current color is.
For PPF only: follow with I will be assisting you with your [vehicle] protection.
For tint only: after greeting go straight into tint conversation.
For multiple services: greet, then on a new line ask what their main goal is. Example: I noticed you selected wrap and PPF, what is your main goal, a full color transformation or mainly protecting the paint?
No emojis ever.
No hyphens or dashes ever.
No bullet points ever.
Warm, genuine, passionate. Short natural texts like a real person.
No generic hype lines.
Reference the actual vehicle by year make model.
Keep responses to 3 to 4 sentences max. Never write a wall of text.
Never confirm who the customer is. You already know from the form."""

    if history and history[-1]["role"] == "assistant":
        history.append({"role": "user", "content": "Continue the conversation naturally."})

    if customer_message:
        history.append({"role": "user", "content": customer_message})

    messages = history if history else [{"role": "user", "content": (
        "New quote request just came in.\n"
        "Name: " + str(name) + "\n"
        "Vehicle: " + str(vehicle) + "\n"
        "Service requested: " + str(service) + "\n"
        "Additional details: " + str(lead.get("details", "None")) + "\n"
        "Premium vehicle: " + str(is_premium) + "\n"
        "High ticket service: " + str(is_high) + "\n\n"
        "Write the first text to send this customer. Follow voice rules exactly. Keep it short."
    )}]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
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

        clear_conversation(clean_phone)

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
                "CUSTOMER REPLIED\n"
                "Name: " + str(lead.get("name", "")) + "\n"
                "Vehicle: " + str(lead.get("vehicle", "")) + "\n\n"
                "Customer said: " + message_text + "\n\n"
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

        msg_id = body.get("id", "")
        if r.get("ai_msg:" + msg_id):
            print("AI message detected by ID - not flagging human active")
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
