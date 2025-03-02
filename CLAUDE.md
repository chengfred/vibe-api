# Vibe API Development Guidelines

## Build & Test Commands
- Create venv: `uv venv`
- Activate venv: `. .venv/bin/activate` (bash/zsh) or `.venv\Scripts\activate` (Windows)
- Run API server: `uv run vibe-api.py` or `python3 vibe-api.py`
  - Dependencies will be auto-detected and installed if needed
- Run type check: `uv pip run mypy vibe-api.py` 
- Format code: `uv pip run black vibe-api.py`
- Lint code: `uv pip run ruff check vibe-api.py`
- Run tests: `uv pip run pytest tests/`
- Run single test: `uv pip run pytest tests/test_file.py::test_function`
- Install development dependencies: `uv pip install -r requirements.txt`
- Set environment variables: `export DB_USER=user DB_PASSWORD=password` (bash/zsh)

## Code Style Guidelines
- Follow PEP 8 conventions for Python
- Use 4 spaces for indentation
- Maximum line length of 100 characters
- Type hints required for all function parameters and return types
- Sort imports alphabetically: standard library, third-party, local
- Use docstrings for all modules, classes and functions
- Error handling: use try/except with specific exceptions, not broad exception catches
- Naming: snake_case for variables/functions, CamelCase for classes
- JSON serialization should use indent=2 for readability

## API Conventions
- Endpoint paths should follow RESTful conventions
- All API responses must be valid JSON
- All data modification requires user confirmation
- Database credentials stored as environment variables
- API methods must include detailed implementation steps
- Error responses should include descriptive messages
- Response format should include a 'status' field
- HTTP status codes should match response content