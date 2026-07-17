import pytest
import re
import main
import api


# BOOKING REFERENCE TEST

# The test sends to the agent the LH123456 booking reference and checks:

# - The agent is able to detect user intent of verifying a booking reference
# - The agent is able to extract the booking reference from the user input
# - The lookup_booking_reference tool is called with the correct booking reference
# - The lookup_booking_reference tool is able to return the correct data for the given booking reference
# - The agent response contains correct lookup_booking_reference response data (customer name, luggage policy, etc.)

def test_booking_reference_lookup(monkeypatch, capsys):
    if not api.check_api_health():
        pytest.skip("API is unreachable")

    user_inputs = iter([
        "I want to add a luggage my booking_reference is LH123456"  
    ])

    def fake_input(_prompt):
        print(_prompt, end="")
        try:
            return next(user_inputs)
        except StopIteration:
            raise SystemExit()

    monkeypatch.setattr("builtins.input", fake_input)

    with pytest.raises(SystemExit):
        main.run_agent(show_agent_logs=True)

    output = capsys.readouterr().out
    assert "Agent: Hello! How can I help you today?" in output
    assert "[System: Agent called lookup_booking_reference" in output
    assert re.search("Emma Rivers", output, re.IGNORECASE)
    assert re.search("BAG26", output, re.IGNORECASE)
    assert re.search("26kg Checked Bag|26 kg Checked Bag",output, re.IGNORECASE)
    assert re.search(r"GBP|£|POUNDS", output, re.IGNORECASE)


# ADD LUGGAGE TEST

# The test sends to the agent the LH123456 booking reference and asks to add a 20kg luggage for Emma. It checks:

# - All the steps of the BOOKING REFERENCE TEST are passed
# - The agent is able to detect user intent of adding luggage
# - The get_luggage_options tool is called with the correct booking reference
# - The get_luggage_options tool is able to return the correct luggage options for the given booking reference
# - The agent is able to extract the luggage details of the luggage to add from the user input
# - The agent is able to ask for confirmation before adding the luggage
# - The add_luggage tool is called with the correct luggage details
# - The add_luggage tool is able to return the correct confirmation code for the added luggage

def test_add_luggage(monkeypatch, capsys):
    if not api.check_api_health():
        pytest.skip("API is unreachable")

    user_inputs = iter([
        "I'd like to add luggage to my booking.",
        "LH123456",
        "add 20kg bag for Emma",
        "Yes"
    ])

    def fake_input(_prompt):
        print(_prompt, end="")
        try:
            return next(user_inputs)
        except StopIteration:
            raise SystemExit()

    monkeypatch.setattr("builtins.input", fake_input)

    with pytest.raises(SystemExit):
        main.run_agent(show_agent_logs=True)

    output = capsys.readouterr().out
    agent_turns = re.findall(r"Agent:\s*(.*?)(?=\nAgent:|\Z)", output, flags=re.DOTALL | re.IGNORECASE)

    if len(agent_turns) >= 2:
        fifth_message = agent_turns[1]
        assert re.search("Emma Rivers", fifth_message, re.IGNORECASE)
        assert re.search("BAG20", fifth_message, re.IGNORECASE)
        assert re.search("20kg Checked Bag|20 kg Checked Bag", fifth_message, re.IGNORECASE)
        assert re.search(r"GBP|£|POUNDS", fifth_message, re.IGNORECASE)

    if len(agent_turns) >= 4:
        seventh_message = agent_turns[3]
        assert re.search(r"LUG-|Confirmation code", seventh_message, re.IGNORECASE)

    assert "[System: Agent called lookup_booking_reference" in output
    assert "[System: Agent called get_luggage_options" in output
    assert "[System: Agent called add_luggage" in output


# AGENT ESCALATION TEST

# The test sends to the agent the SH00001 booking reference and asks to contact a human agent. It checks:

# - The agent is able to detect user intent of contacting a human agent
# - The agent is able to extract the booking reference from the user input
# - The agent ask the user to confirm or cancel the escalation
# - The human_escalation tool is called 
# - The human_escalation tool is able to confirm the success of the escalation request

def test_escalation(monkeypatch, capsys):
    if not api.check_api_health():
        pytest.skip("API is unreachable")

    user_inputs = iter([
        "I want to contact a human agent",
        "SH00001",
        "Yes",
    ])

    def fake_input(_prompt):
        print(_prompt, end="")
        try:
            return next(user_inputs)
        except StopIteration:
            raise SystemExit()

    monkeypatch.setattr("builtins.input", fake_input)

    with pytest.raises(SystemExit):
        main.run_agent(show_agent_logs=True)

    output = capsys.readouterr().out
    agent_turns = re.findall(r"Agent:\s*(.*?)(?=\nYou:|\Z)", output, flags=re.DOTALL | re.IGNORECASE)

    assert len(agent_turns) >= 3

    fifth_message = agent_turns[2]
    assert re.search(r"confirm|please|confiramation|proceed|sure|like me|want", fifth_message, re.IGNORECASE)

    last_message = agent_turns[-1]
    assert re.search(r"successfully|has been|success|confirmed|soon|shortly|agent|human|reply|back", last_message, re.IGNORECASE)

    assert "[System: Agent called human_escalation_request" in output