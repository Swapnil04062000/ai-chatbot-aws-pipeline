# AI Chat

A simple ChatGPT-style chat app built with FastAPI and vanilla HTML/CSS/JS.

## Features

- Dark-themed chat UI with sidebar and chat history
- Conversation history saved in the browser (localStorage)
- OpenAI API integration (optional — works in demo mode without a key)

## Setup

1. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. (Optional) Copy `.env.example` to `.env` and add your OpenAI API key:

```bash
copy .env.example .env
```

3. Run the server:

```bash
uvicorn main:app --reload
```

4. Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

## Demo mode

If no `OPENAI_API_KEY` is set, the app runs in demo mode and echoes your messages back with a notice to add an API key.
