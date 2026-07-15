# Seed Code Architecture

## Technical Stack

Language
- Python 3.12+

Libraries

- OpenAI SDK
- Rich
- Prompt Toolkit
- Pydantic
- httpx

---

## Folder Structure

seedcode/

├── seedcode/
│
├── cli.py
├── chat.py
├── config.py
├── commands.py
├── storage.py
├── models.py
├── ui.py
├── validator.py
├── utils.py
│
├── assets/
│
├── config/
│
├── history/
│
├── pyproject.toml
├── README.md
└── LICENSE

---

## Flow

seedcode

↓

Load Config

↓

API Key Exists?

├── Yes
│
↓
Start Chat

└── No

↓

Ask API Key

↓

Validate

↓

Save

↓

Start Chat

---

Chat Flow

User Input

↓

Command?

├── Yes

↓

Execute Command

└── No

↓

Send to OpenRouter

↓

Stream Response

↓

Save Conversation

↓

Wait for Next Prompt

file 

Seed-Code/
│
├── seedcode/                    # Main Python package
│   │
│   ├── __init__.py
│   ├── __main__.py              # python -m seedcode
│   │
│   ├── cli.py                   # Main CLI entry point
│   ├── app.py                   # Application controller
│   │
│   ├── core/                    # Core logic
│   │   ├── __init__.py
│   │   ├── chat.py              # Chat engine
│   │   ├── client.py            # OpenRouter API client
│   │   ├── models.py            # AI model handling
│   │   └── streaming.py         # Response streaming
│   │
│   ├── config/                  # Configuration system
│   │   ├── __init__.py
│   │   ├── manager.py           # Load/save config
│   │   └── defaults.py
│   │
│   ├── commands/                # CLI commands
│   │   ├── __init__.py
│   │   ├── help.py
│   │   ├── clear.py
│   │   ├── history.py
│   │   └── about.py
│   │
│   ├── memory/                  # Conversation memory
│   │   ├── __init__.py
│   │   ├── storage.py
│   │   └── manager.py
│   │
│   ├── ui/                      # Terminal UI
│   │   ├── __init__.py
│   │   ├── banner.py            # Seed Code logo
│   │   ├── theme.py             # Green theme
│   │   ├── prompts.py
│   │   └── renderer.py
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logger.py
│       └── helpers.py
│
│
├── data/                        # User data
│   │
│   ├── config.json              # API key/settings
│   └── history/
│       └── chats.json
│
│
├── tests/                       # Testing
│   ├── test_config.py
│   ├── test_client.py
│   └── test_memory.py
│
│
├── docs/
│   └── screenshots/
│
│
├── Info_for_ai_agents/          # AI instructions
│   ├── PRD.md
│   ├── Architecture.md
│   ├── Rules.md
│   ├── Phases.md
│   ├── Design.md
│   └── Memory.md
│
│
├── .gitignore
├── LICENSE
├── README.md
├── requirements.txt
├── pyproject.toml
└── install.sh