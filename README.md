# Loveholidays Luggage Assistant - Federico Schiocchet

This project runs a CLI assistant for booking luggage management.

## Prerequisites

- Python 3.10+
- Internet connection
- An OpenAI API key

## 1) Clone or download the project

Copy this folder to your machine, then open a terminal in the project root.

## 2) Install dependencies

```bash
pip install openai pydantic requests python-dotenv pytest
```

## 3) Configure environment variables

Edit the `.env` file and set the OpenAI API key:

```env
OPENAI_API_KEY=your_openai_api_key_here
```

## 4) Run the application

```bash
python main.py
```

The assistant will start in the terminal and prompt for user input.

## 5) Run the automated tests

Run the following commands from the project root:

```bash
python.exe -m pytest tests.py::test_booking_reference_lookup -v
python.exe -m pytest tests.py::test_add_luggage -v
python.exe -m pytest tests.py::test_escalation -v
```

## Notes

- To enable agent logs, set `show_agent_logs=True` in `main.py`. An `app.log` file will be created in the project root folder.
 
