import requests
import state
from config import API_BASE_URL
from utils import log_event


# API functions for checking if the Loveholidays Mock API is healthy.

def check_api_health():
    try:
        response = requests.get(f"{API_BASE_URL}health", timeout=5)
        response_data = response.json()
        return response_data.get("status") == "ok"
    except requests.RequestException:
        log_event("check_api_health failed due to request exception")
        return False


# Validates a booking reference and returns the relevant booking details if valid.

def lookup_booking_reference(booking_reference: str):
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


# Returns the list of available luggage options for a given booking reference, filtering out any options that are not available.

def _filter_available_options(options):
    filtered_passengers = {}

    for passenger_id, passenger_bags in options.items():
        available_luggages = {}
        
        for option_key, luggage in passenger_bags.items():
            if luggage.get("quantityAvailable", 0) <= 0:
                continue
            available_luggages[option_key] = luggage
            
        if not available_luggages:
            continue   
        filtered_passengers[passenger_id] = available_luggages

    return filtered_passengers


# Builds a dictionary of service definitions from the provided data.

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


# Returns the leg of the flight based on the flight segments. If there are multiple segments, it returns "return". 
# If there is only one segment, it checks if it is outbound or inbound.
# A leg is a single, continuous segment of a journey from takeoff to landing

def _get_leg(segments):
    if len(segments) > 1:
        return "return"
    return "outbound" if segments[0].split("-")[-1].lower() == "out" else "inbound"


# Check if a luggage type has multiple direction options (multiple luggage options for multiple flights).
# If not, there is no need to append the leg suffix (e.g. "outbound" or "return") to the luggage name as we assume the user knows the direction.

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


# Builds a selection option dictionary for a given ancillary service selection.

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


# Assigns the available luggage types to the corresponding passengers.

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


# Validates the luggage request against the available options.

def _validate_luggage_request(options, luggages):
    for luggage in luggages:
        passenger_id = luggage.get("passenger_id")
        luggage_type = luggage.get("type")
        quantity = luggage.get("quantity", 0)

        if passenger_id not in options:
            return {"error": f"Passenger {passenger_id} not found"}

        if luggage_type not in options[passenger_id]:
            return {"error": f"Bag {luggage_type} not found for passenger {passenger_id}"}

        if options[passenger_id][luggage_type]["quantityAvailable"] < quantity:
            return {"error": f"Not enough availability for {luggage_type} for passenger {passenger_id}"}

    return None


# Checks if there is a mismatch between the payload items and the response items.
# This check is important to ensure that the items added to the booking match what was requested.

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


# Extracts the error message from the add luggage response.

def _extract_add_luggage_error(resp_data):
    error_details = resp_data.get("detail", {})
    
    if isinstance(error_details, dict) and error_details.get("error") :
        if error_details.get("message"):
            return error_details.get("message")
    if isinstance(error_details, list) and len(error_details) > 0:
        if error_details[0].get("msg"):
            return error_details[0].get("msg")

    return "Operation unsuccessful"


# Gets available luggage options for a valid booking reference.

def get_luggage_options(booking_reference: str):
    cache_entry = state.current_luggage_options .get(booking_reference)
    if cache_entry is not None:
        return _filter_available_options(cache_entry)

    try:
        response = requests.post(
            f"{API_BASE_URL}booking/luggage-options",
            json={"bookingReference": booking_reference}
        )

        if response.status_code != 200:
            return {"error": "Unable to fetch luggage options"}

        data = response.json()
        if not data.get("canAddLuggage", False):
            state.current_luggage_options [booking_reference] = {}
            return {}

        service_definitions = _build_service_definitions(data.get("serviceDefinitions", []))
        ancillary_services = data.get("ancillaryServices", [])
        if not ancillary_services or not service_definitions:
            state.current_luggage_options [booking_reference] = {}
            return {}

        has_multiple_directions = _has_multiple_directions(ancillary_services)
        passenger_options = _build_passenger_options(
            ancillary_services,
            service_definitions,
            data.get("currency", "GBP"),
            has_multiple_directions
        )

        state.current_luggage_options [booking_reference] = passenger_options
        return _filter_available_options(passenger_options)

    except Exception as e:
        log_event(f"get_luggage_options exception for booking '{booking_reference}': {e}")
        return {"error": "System error", "exception": str(e)}
    

# Adds luggages to a booking. Ex: luggages=[{'type': 'BAG20', 'quantity': 1, 'passenger_id': 'PAX-1001'}]    
    
def add_luggage(booking_reference: str, luggages: list):
    options = state.current_luggage_options .get(booking_reference)
    
    if not options:
        return {"error": "Booking reference not found in active session"}
        
    validation_error = _validate_luggage_request(options, luggages)
    if validation_error:
        return validation_error
            
    results = []
    
    for luggage in luggages:
        passenger_id = luggage.get("passenger_id")
        luggage_type = luggage.get("type")
        quantity = luggage.get("quantity", 0)
        
        option = options[passenger_id][luggage_type]
        service_definition_id = option["id"]
        
        payload = {
            "bookingReference": booking_reference,
            "idempotencyKey": f"add-luggage-{booking_reference}-{passenger_id.replace('-', '')}-{luggage_type}",
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
            response = requests.post(f"{API_BASE_URL}booking/add-luggage", json=payload)
            response_data = response.json()

            if response_data.get("success") is not True:
                error_message = _extract_add_luggage_error(response_data)
                if error_message == "Item 1 has already been added to this booking.":
                    options[passenger_id][luggage_type]["quantityAvailable"] = 0 
                return {"error": error_message}

            if _payload_items_mismatch(payload["items"], response_data.get("addedItems", [])):
                return {"error": "Baggage mismatch.", "error_code": "baggage_mismatch", "response": response_data}

            state.current_luggage_options [booking_reference][passenger_id][luggage_type]["quantityAvailable"] -= quantity
            results.append({"success": True, "confirmation_code": response_data.get("confirmationCode", "")})
        except Exception as e:
            log_event(f"add_luggage exception for booking '{booking_reference}': {e}")
            return {"error": f"API request failed", "exception": str(e)}
            
    return {"success": True, "results": results}


# Send a human escalation request to Loveholidays. 

def escalation(booking_reference: str, reason: str, user_message: str):
    payload = {
        "bookingReference": booking_reference,
        "reason": reason,
        "customerMessage": user_message
    }
    response = requests.post(f"{API_BASE_URL}escalations", json=payload)
    return response.json()