import requests
import state
from config import API_BASE_URL
from utils import log_event


def check_api_health():
    """Checks if the mock API is healthy before starting."""
    try:
        response = requests.get(f"{API_BASE_URL}health", timeout=5)
        response_data = response.json()
        return response_data.get("status") == "ok"
    except requests.RequestException:
        log_event("check_api_health failed due to request exception")
        return False


def lookup_booking_reference(booking_reference: str):
    """Looks up a booking by reference."""
    try:
        payload = {
            "bookingReference": booking_reference
        }
        response = requests.post(f"{API_BASE_URL}booking/lookup", json=payload)
        if response.status_code != 200:
            return {"error": "Reference is not valid"}
            
        data = response.json()
        if data.get("bookingReference") == booking_reference:
            passengers = []
            for passenger in data.get("passengers", []):
                passengers.append({
                    "id": passenger.get("passengerId"),
                    "firstName": passenger.get("firstName"),
                    "surname": passenger.get("surname")
                })
            return {
                "bookingReference": data.get("bookingReference"),
                "customerName": data.get("customerName"),
                "canAddLuggage": data.get("canAddLuggage", False),
                "luggagePolicy": data.get("luggagePolicy", ""),
                "passengers": passengers
            }
        return {"error": "Reference is not valid"}
    except Exception as e:
        log_event(f"lookup_booking_reference exception for booking '{booking_reference}': {e}")
        return {"error": "System error", "exception": str(e)}


def _filter_available_options(options):
    filtered_passengers = {}

    for passenger_id, passenger_bags in options.items():
        available_baggages = {}
        
        for option_key, baggage in passenger_bags.items():
            if baggage.get("quantityAvailable", 0) <= 0:
                continue
            available_baggages[option_key] = baggage
            
        if not available_baggages:
            continue   
        filtered_passengers[passenger_id] = available_baggages

    return filtered_passengers


def _build_service_definitions(service_definitions_data):
    service_definitions = {}
    for item in service_definitions_data:
        service_id = item.get("serviceDefinitionId")
        if not service_id:
            continue
        description = item.get("descriptions", [{}])[0].get("text", "")
        service_definitions[service_id] = {
            "name": item.get("name"),
            "descr": description,
        }
    return service_definitions


def _get_leg(segments):
    if len(segments) > 1:
        return "return"
    return "outbound" if segments[0].split("-")[-1].lower() == "out" else "inbound"


def _has_multiple_directions(ancillary_services):
    previous = None
    
    for ancillary_service in ancillary_services:
        for option in ancillary_service.get("selectionOptions", []):
            segments = option.get("flightSegmentRefIds", [])
            
            if option.get("quantityAvailable", 0) <= 0 or not segments:
                continue
            leg = _get_leg(segments)
            
            if previous is not None and leg != previous:
                return True
            previous = leg
            
    return False


def _build_selection_option(selection, option_context):
    quantity = selection.get("quantityAvailable", 0)
    segments = selection.get("flightSegmentRefIds", [])
    
    if quantity <= 0 or not segments:
        return None

    leg_suffix = _get_leg(segments)
    service_definition_id = option_context["svcId"]
    service_info = option_context["serviceInfo"]
    option_key = f"{service_definition_id}_{leg_suffix}" if option_context["hasMultipleDirections"] else service_definition_id
    option_name = service_info["name"]
    
    if option_context["hasMultipleDirections"]:
        option_name = f"{service_info['name']} ({leg_suffix.capitalize()})"

    return {
        "optionKey": option_key,
        "optionData": {
            "id": service_definition_id,
            "optionKey": option_key,
            "name": option_name,
            "descr": service_info["descr"],
            "quantityAvailable": quantity,
            "flightSegmentRefIds": list(segments),
            "unitPrice": option_context["ancillaryService"].get("unitPrice", 0),
            "currency": option_context["currency"],
            "ancillaryServiceId": option_context["ancillaryService"].get("ancillaryServiceId", ""),
        },
    }


def _assign_option_to_passengers(passenger_options, selection, option_entry):
    option_key = option_entry["optionKey"]
    option_data = option_entry["optionData"]

    for passenger_id in selection.get("passengerRefIds", []):
        if passenger_id not in passenger_options:
            passenger_options[passenger_id] = {}
        passenger_options[passenger_id][option_key] = option_data.copy()


def _build_passenger_options(ancillary_services, service_definitions, currency, has_multiple_directions):
    passenger_options = {}

    for ancillary_service in ancillary_services:
        service_definition_id = ancillary_service.get("serviceDefinitionRefId")
        if not service_definition_id:
            continue

        service_info = service_definitions.get(service_definition_id)
        if not service_info:
            continue

        option_context = {
            "ancillaryService": ancillary_service,
            "serviceInfo": service_info,
            "svcId": service_definition_id,
            "currency": currency,
            "hasMultipleDirections": has_multiple_directions
        }

        for option in ancillary_service.get("selectionOptions", []):
            option_entry = _build_selection_option(option, option_context)
            if not option_entry:
                continue
            _assign_option_to_passengers(passenger_options, option, option_entry)

    return passenger_options


def _validate_luggage_request(options, baggages):
    for baggage in baggages:
        passenger_id = baggage.get("passenger_id")
        baggage_type = baggage.get("type")
        quantity = baggage.get("quantity", 0)

        if passenger_id not in options:
            return {"error": f"Passenger {passenger_id} not found"}

        if baggage_type not in options[passenger_id]:
            return {"error": f"Bag {baggage_type} not found for passenger {passenger_id}"}

        if options[passenger_id][baggage_type]["quantityAvailable"] < quantity:
            return {"error": f"Not enough availability for {baggage_type} for passenger {passenger_id}"}

    return None


def _payload_items_mismatch(payload_items, response_items):
    if len(payload_items) != len(response_items):
        return True

    payload_keys = sorted(
        (item.get("serviceDefinitionId"), item.get("ancillaryServiceId"))
        for item in payload_items
    )
    response_keys = sorted(
        (item.get("serviceDefinitionId"), item.get("ancillaryServiceId"))
        for item in response_items
    )

    if payload_keys != response_keys:
        return True

    return False


def _extract_add_luggage_error(resp_data):
    error_details = resp_data.get("detail", {})
    
    if isinstance(error_details, dict) and error_details.get("error") :
        if error_details.get("message"):
            return error_details.get("message")
    if isinstance(error_details, list) and len(error_details) > 0:
        if error_details[0].get("msg"):
            return error_details[0].get("msg")

    return "Operation unsuccessful"


def get_luggage_options(booking_reference: str):
    """Gets available luggage options for a valid booking reference."""

    cache_entry = state.current_luggage_options .get(booking_reference)
    if cache_entry is not None:
        return _filter_available_options(cache_entry)

    try:
        resp = requests.post(
            f"{API_BASE_URL}booking/luggage-options",
            json={"bookingReference": booking_reference}
        )

        if resp.status_code != 200:
            return {"error": "Unable to fetch luggage options"}

        data = resp.json()
        if not data.get("canAddLuggage", False):
            state.current_luggage_options [booking_reference] = {}
            return {}

        service_definitions = _build_service_definitions(data.get("serviceDefinitions", []))
        ancillary_services = data.get("ancillaryServices", [])
        if not ancillary_services or not service_definitions:
            state.current_luggage_options [booking_reference] = {}
            return {}

        has_multiple_directions = _has_multiple_directions(ancillary_services)
        pax_options = _build_passenger_options(
            ancillary_services,
            service_definitions,
            data.get("currency", "GBP"),
            has_multiple_directions
        )

        state.current_luggage_options [booking_reference] = pax_options
        return _filter_available_options(pax_options)

    except Exception as e:
        log_event(f"get_luggage_options exception for booking '{booking_reference}': {e}")
        return {"error": "System error", "exception": str(e)}
    
def add_luggage(booking_reference: str, baggages: list):
    """Adds luggages to a booking. Ex: baggages=[{'type': 'BAG20', 'quantity': 1, 'passenger_id': 'PAX-1001'}]"""
    options = state.current_luggage_options .get(booking_reference)
    
    if not options:
        return {"error": "Booking reference not found in active session"}
        
    validation_error = _validate_luggage_request(options, baggages)
    if validation_error:
        return validation_error
            
    results = []
    
    for b in baggages:
        passenger_id = b.get("passenger_id")
        baggage_type = b.get("type")
        quantity = b.get("quantity", 0)
        
        option = options[passenger_id][baggage_type]
        service_definition_id = option["id"]
        
        payload = {
            "bookingReference": booking_reference,
            "idempotencyKey": f"add-luggage-{booking_reference}-{passenger_id.replace('-', '')}-{baggage_type}",
            "items": [{
                "ancillaryServiceId": option["ancillaryServiceId"],
                "expectedPrice": {
                    "amount": option["unitPrice"],
                    "currency": option["currency"]
                },
                "flightSegmentRefIds": option["flightSegmentRefIds"],
                "passengerRefIds": [passenger_id],
                "quantity": quantity,
                "serviceDefinitionId": service_definition_id
            }]
        }
        
        try:
            resp = requests.post(f"{API_BASE_URL}booking/add-luggage", json=payload)
            resp_data = resp.json()

            if resp_data.get("success") is not True:
                error_message = _extract_add_luggage_error(resp_data)
                if error_message == "Item 1 has already been added to this booking.":
                    options[passenger_id][baggage_type]["quantityAvailable"] = 0 
                return {"error": error_message}

            if _payload_items_mismatch(payload["items"], resp_data.get("addedItems", [])):
                return {"error": "Baggage mismatch.", "error_code": "baggage_mismatch", "response": resp_data}

            state.current_luggage_options [booking_reference][passenger_id][baggage_type]["quantityAvailable"] -= quantity
            results.append({"success": True, "confirmation_code": resp_data.get("confirmationCode", "")})
        except Exception as e:
            log_event(f"add_luggage exception for booking '{booking_reference}': {e}")
            return {"error": f"API request failed", "exception": str(e)}
            
    return {"success": True, "results": results}

def escalation(booking_reference: str, reason: str, user_message: str):
    payload = {
        "bookingReference": booking_reference,
        "reason": reason,
        "customerMessage": user_message
    }
    response = requests.post(f"{API_BASE_URL}escalations", json=payload)
    return response.json()