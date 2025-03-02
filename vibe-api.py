#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "markdown",
#     "openai",
#     "psycopg2",
# ]
# ///

import os
import re
import json
import socket
import getpass
import argparse
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from json import JSONEncoder
import threading
import sqlite3
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import openai
import readline
import markdown

CONFIG_FILE = "vibe-api.txt"


# Custom JSON encoder to handle datetime objects
class DateTimeEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().default(obj)


class APIServer:
    def __init__(self, config_file=CONFIG_FILE):
        self.config_file = config_file
        self.config = {}
        self.apis = []
        self.data_modification_permission = False
        self.load_config()
        self.openai_client = openai.OpenAI()

        # Setup database connection if configured
        if self.has_database_config():
            self.setup_database_env()

    def load_config(self):
        """Load configuration from the config file"""
        if not os.path.exists(self.config_file):
            print(f"Configuration file {self.config_file} not found.")
            return

        with open(self.config_file, "r") as file:
            content = file.read()

            # Extract database info
            db_section = re.search(
                r"## Database Info\n```json\n(.*?)\n```", content, re.DOTALL
            )
            if db_section:
                try:
                    self.config["database"] = json.loads(db_section.group(1))
                except json.JSONDecodeError:
                    print("Error: Invalid JSON in database configuration.")

            # Extract API definitions
            api_sections = re.findall(
                r"### API: (.*?)\n#### HTTP Method\n(.*?)\n#### Path\n(.*?)\n#### Description\n(.*?)\n#### Implementation\n```\n(.*?)\n```",
                content,
                re.DOTALL,
            )

            self.apis = []
            for name, method, path, description, implementation in api_sections:
                self.apis.append(
                    {
                        "name": name.strip(),
                        "method": method.strip(),
                        "path": path.strip(),
                        "description": description.strip(),
                        "implementation": implementation.strip(),
                    }
                )

    def save_config(self):
        """Save configuration to the config file"""
        content = "# Vibe API Configuration\n\n"

        # Add database info
        if "database" in self.config:
            content += (
                "## Database Info\n```json\n"
                + json.dumps(self.config["database"], indent=2, cls=DateTimeEncoder)
                + "\n```\n\n"
            )

        # Add API list summary
        if self.apis:
            content += "## API Endpoints\n\n"
            for api in self.apis:
                content += (
                    f"- **{api['method']}** - {api['path']} - {api['description']}\n"
                )
            content += "\n"

        # Add detailed API definitions
        if self.apis:
            content += "## API Definitions\n\n"
            for api in self.apis:
                content += f"### API: {api['name']}\n"
                content += f"#### HTTP Method\n{api['method']}\n"
                content += f"#### Path\n{api['path']}\n"
                content += f"#### Description\n{api['description']}\n"
                content += f"#### Implementation\n```\n{api['implementation']}\n```\n\n"

        with open(self.config_file, "w") as file:
            file.write(content)

        print(f"Configuration saved to {self.config_file}")

    def has_database_config(self):
        """Check if database configuration exists"""
        return "database" in self.config and "connection" in self.config["database"]

    def setup_database_env(self):
        """Set up database environment variables"""
        if not self.has_database_config():
            return False

        db_config = self.config["database"]["connection"]

        # Check for password in environment
        password_env = db_config.get("password_env_var", "DB_PASSWORD")
        if not os.environ.get(password_env):
            print(
                f"Database password not found in environment variable {password_env}."
            )
            print(f"Please set {password_env} environment variable or enter it now.")
            try:
                password = getpass.getpass(
                    "Enter database password (leave empty to skip): "
                )
                if password:
                    os.environ[password_env] = password
                    print("Database password set for this session.")
            except Exception as e:
                print(f"Could not get password: {e}")
                print(
                    f"You may need to set {password_env} environment variable manually."
                )

        # Check for user in environment
        user_env = db_config.get("user_env_var", "DB_USER")
        if not os.environ.get(user_env):
            if "user" in db_config:
                os.environ[user_env] = db_config["user"]
                print(f"Using database user from config: {db_config['user']}")
            else:
                print(
                    f"Database user not found in environment variable {user_env}."
                )
                print(f"Please set {user_env} environment variable or enter it now.")
                try:
                    username = input("Enter database username: ")
                    if username:
                        os.environ[user_env] = username
                        print("Database username set for this session.")
                except Exception as e:
                    print(f"Could not get username: {e}")
                    print(
                        f"You may need to set {user_env} environment variable manually."
                    )

        return True

    def setup_database(self):
        """Setup database connection and introspect schema"""
        print("\n=== Database Setup ===")
        db_url = input(
            "Enter a database connection URL (e.g., postgresql://localhost/mydatabase): "
        )

        # Parse the URL
        parsed_url = urlparse(db_url)
        db_type = parsed_url.scheme

        if db_type == "postgresql":
            # Get username and password if not in URL
            username = parsed_url.username or input("Database username: ")
            password = parsed_url.password or getpass.getpass("Database password: ")

            # Connect to the database
            conn_params = {
                "dbname": parsed_url.path[1:],
                "user": username,
                "password": password,
                "host": parsed_url.hostname or "localhost",
                "port": parsed_url.port or 5432,
            }

            try:
                # Connect to PostgreSQL
                conn = psycopg2.connect(**conn_params)
                conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

                # Store connection info
                self.config["database"] = {
                    "type": "postgresql",
                    "connection": {
                        "dbname": conn_params["dbname"],
                        "host": conn_params["host"],
                        "port": conn_params["port"],
                        # Use environment variables for credentials
                        "password_env_var": "DB_PASSWORD",
                        "user_env_var": "DB_USER",
                    },
                }

                # Set the environment variables for future use
                os.environ["DB_USER"] = username
                os.environ["DB_PASSWORD"] = password

                # Introspect the database
                self._introspect_postgres(conn)
                conn.close()

                print("\nDatabase connection successful and schema introspected.")

            except Exception as e:
                print(f"Error connecting to PostgreSQL: {e}")
                return False

        else:
            print(f"Unsupported database type: {db_type}")
            print("Currently supported: postgresql")
            return False

        self.save_config()
        return True

    def _introspect_postgres(self, conn):
        """Introspect PostgreSQL database schema"""
        cursor = conn.cursor()

        # Get schemas excluding system schemas
        cursor.execute("""
            SELECT schema_name 
            FROM information_schema.schemata 
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema')
        """)
        schemas = [row[0] for row in cursor.fetchall()]

        db_schema = {}

        for schema in schemas:
            # Get tables
            cursor.execute(
                """
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = %s
                AND table_type = 'BASE TABLE'
            """,
                (schema,),
            )

            tables = [row[0] for row in cursor.fetchall()]
            db_schema[schema] = {}

            for table in tables:
                # Get columns
                cursor.execute(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                """,
                    (schema, table),
                )

                columns = [
                    {"name": row[0], "type": row[1], "nullable": row[2] == "YES"}
                    for row in cursor.fetchall()
                ]

                # Get primary keys
                cursor.execute(
                    """
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                    AND tc.table_schema = %s
                    AND tc.table_name = %s
                """,
                    (schema, table),
                )

                primary_keys = [row[0] for row in cursor.fetchall()]

                # Get foreign keys
                cursor.execute(
                    """
                    SELECT
                        kcu.column_name,
                        ccu.table_schema AS foreign_table_schema,
                        ccu.table_name AS foreign_table_name,
                        ccu.column_name AS foreign_column_name
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                    ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage AS ccu
                    ON ccu.constraint_name = tc.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND tc.table_schema = %s
                    AND tc.table_name = %s
                """,
                    (schema, table),
                )

                foreign_keys = [
                    {
                        "column": row[0],
                        "references": {
                            "schema": row[1],
                            "table": row[2],
                            "column": row[3],
                        },
                    }
                    for row in cursor.fetchall()
                ]

                # Get indexes
                cursor.execute(
                    """
                    SELECT
                        i.relname AS index_name,
                        a.attname AS column_name
                    FROM
                        pg_class t,
                        pg_class i,
                        pg_index ix,
                        pg_attribute a,
                        pg_namespace n
                    WHERE
                        t.oid = ix.indrelid
                        AND i.oid = ix.indexrelid
                        AND a.attrelid = t.oid
                        AND a.attnum = ANY(ix.indkey)
                        AND t.relkind = 'r'
                        AND t.relname = %s
                        AND n.oid = t.relnamespace
                        AND n.nspname = %s
                """,
                    (table, schema),
                )

                indexes = {}
                for row in cursor.fetchall():
                    index_name, column_name = row
                    if index_name not in indexes:
                        indexes[index_name] = []
                    indexes[index_name].append(column_name)

                # Store table info
                db_schema[schema][table] = {
                    "columns": columns,
                    "primary_keys": primary_keys,
                    "foreign_keys": foreign_keys,
                    "indexes": [
                        {"name": name, "columns": cols}
                        for name, cols in indexes.items()
                    ],
                }

        # Store the schema information
        self.config["database"]["schema"] = db_schema

    def execute_db_query(self, query, params=None, read_only=True):
        """Execute a database query"""
        if not self.has_database_config():
            return {"error": "No database connection configured"}

        if self.config["database"]["type"] == "postgresql":
            try:
                db_config = self.config["database"]["connection"]
                # Get connection parameters from environment or configuration
                conn_params = {
                    "dbname": db_config["dbname"],
                    "host": db_config["host"],
                    "port": db_config["port"],
                    "user": os.environ.get(
                        db_config.get("user_env_var", "DB_USER"), ""
                    ),
                    "password": os.environ.get(
                        db_config.get("password_env_var", "DB_PASSWORD"), ""
                    ),
                }

                # Verify credentials are available
                if not conn_params["password"]:
                    return {
                        "error": "Database password not set",
                        "details": f"Please set {db_config.get('password_env_var', 'DB_PASSWORD')} environment variable",
                    }
                
                if not conn_params["user"]:
                    return {
                        "error": "Database user not set",
                        "details": f"Please set {db_config.get('user_env_var', 'DB_USER')} environment variable",
                    }

                # Connect with connection timeout
                conn = psycopg2.connect(**conn_params, connect_timeout=10)
                cursor = conn.cursor()

                # Check if it's a data modification operation
                is_modification = False
                if not read_only:
                    lower_query = query.lower().strip()
                    is_modification = any(
                        lower_query.startswith(op)
                        for op in [
                            "insert",
                            "update",
                            "delete",
                            "drop",
                            "create",
                            "alter",
                        ]
                    )

                # Ask for permission if it's a data modification
                if is_modification and not self.data_modification_permission:
                    print("\n=== Data Modification Request ===")
                    print(f"Query: {query}")
                    if params:
                        print(f"Parameters: {params}")

                    response = input("Allow this operation? (y/n/all): ")

                    if response.lower() == "all":
                        self.data_modification_permission = True
                    elif response.lower() != "y":
                        return {"error": "Operation not authorized by user"}

                # Execute the query
                cursor.execute(query, params or [])

                # Get column names if this is a SELECT query
                result = {"status": "success"}
                if cursor.description:
                    column_names = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()
                    result["data"] = [dict(zip(column_names, row)) for row in rows]
                    result["rowCount"] = len(rows)
                else:
                    result["rowCount"] = cursor.rowcount

                # Commit if it's a modification
                if is_modification:
                    conn.commit()

                cursor.close()
                conn.close()

                return result

            except psycopg2.OperationalError as e:
                return {"error": "Database connection failed", "details": str(e)}
            except Exception as e:
                import traceback

                return {
                    "error": "Query execution failed",
                    "details": str(e),
                    "query": query,
                    "traceback": traceback.format_exc(),
                }
        else:
            return {
                "error": f"Unsupported database type: {self.config['database']['type']}"
            }

    def show_api_list(self):
        """Show the list of current APIs"""
        print("\n=== Current API Endpoints ===")

        if not self.apis:
            print("No APIs defined yet")
        else:
            for i, api in enumerate(self.apis, 1):
                print(f"{i}. {api['method']} - {api['path']} - {api['description']}")

    def add_api(self):
        """Add a new API endpoint"""
        print("\n=== Add New API Endpoint ===")

        # Get path
        path = input("Enter API path (e.g., /users/rooms): ")

        # Get description
        print("\nDescribe what this API should do:")
        description = input("> ")

        # Use LLM to format and enhance the description
        response = self.openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": """
                You are helping to create an API endpoint. Given a user's description,
                you'll provide:
                1. A concise one-line description of what the API does
                2. A detailed step-by-step implementation plan
                3. The most appropriate HTTP method (GET, POST, PUT, DELETE, etc.)
                4. A response schema that defines the expected JSON structure of the API response
                
                Format your response as a JSON object with these fields:
                {
                    "method": "HTTP_METHOD",
                    "concise_description": "Short description",
                    "implementation": "Detailed step-by-step implementation plan including:\n1. The steps to handle the request\n2. The expected response schema in JSON format\n3. Error handling guidelines"
                }
                
                Make sure to include a specific JSON schema example in the implementation steps.
                """,
                },
                {
                    "role": "user",
                    "content": f"""
                API Path: {path}
                User Description: {description}
                Database Schema: {json.dumps(self.config.get("database", {}).get("schema", {}))}
                
                IMPORTANT: Return your response as a valid JSON object matching the format described above.
                """,
                },
            ],
        )

        # Parse the JSON response
        try:
            content = response.choices[0].message.content
            api_info = self._extract_json_from_llm_response(
                content,
                {
                    "method": "GET",
                    "concise_description": description,
                    "implementation": "1. Parse request parameters\n2. Query the database\n3. Return results",
                },
            )

            # Create a name from the path
            api_name = path.strip("/").replace("/", "_")

            # Add the new API
            new_api = {
                "name": api_name,
                "method": api_info["method"],
                "path": path,
                "description": api_info["concise_description"],
                "implementation": api_info["implementation"],
            }

            self.apis.append(new_api)

            # Save the configuration
            self.save_config()

            print(f"\nAPI endpoint added: {new_api['method']} {new_api['path']}")
            print(f"Description: {new_api['description']}")

        except Exception as e:
            print(f"Error creating API endpoint: {e}")

    def update_api(self):
        """Update an existing API endpoint"""
        self.show_api_list()

        if not self.apis:
            return

        try:
            choice = int(input("\nEnter the number of the API to update: "))
            if choice < 1 or choice > len(self.apis):
                print("Invalid selection")
                return

            api = self.apis[choice - 1]

            print(f"\nUpdating API: {api['method']} {api['path']}")
            print(f"Current description: {api['description']}")

            # Get new description
            print("\nEnter new description (leave empty to keep current):")
            description = input("> ")

            if description:
                # Use LLM to format and enhance the description
                response = self.openai_client.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {
                            "role": "system",
                            "content": """
                        You are helping to update an API endpoint. Given a user's description,
                        you'll provide:
                        1. A concise one-line description of what the API does
                        2. A detailed step-by-step implementation plan
                        3. The most appropriate HTTP method (GET, POST, PUT, DELETE, etc.)
                        4. A response schema that defines the expected JSON structure of the API response
                        
                        Format your response as a JSON object with these fields:
                        {
                            "method": "HTTP_METHOD",
                            "concise_description": "Short description",
                            "implementation": "Detailed step-by-step implementation plan including:\n1. The steps to handle the request\n2. The expected response schema in JSON format\n3. Error handling guidelines"
                        }
                        
                        Make sure to include a specific JSON schema example in the implementation steps.
                        """,
                        },
                        {
                            "role": "user",
                            "content": f"""
                        API Path: {api["path"]}
                        User Description: {description}
                        Database Schema: {json.dumps(self.config.get("database", {}).get("schema", {}))}
                        
                        IMPORTANT: Return your response as a valid JSON object matching the format described above.
                        """,
                        },
                    ],
                )

                # Parse the response
                content = response.choices[0].message.content
                api_info = self._extract_json_from_llm_response(
                    content,
                    {
                        "method": api["method"],
                        "concise_description": description or api["description"],
                        "implementation": api["implementation"],
                    },
                )

                # Update the API
                api["method"] = api_info["method"]
                api["description"] = api_info["concise_description"]
                api["implementation"] = api_info["implementation"]

                # Save the configuration
                self.save_config()

                print(f"\nAPI endpoint updated: {api['method']} {api['path']}")
                print(f"New description: {api['description']}")
            else:
                print("No changes made")

        except ValueError:
            print("Please enter a valid number")

    def delete_api(self):
        """Delete an existing API endpoint"""
        self.show_api_list()

        if not self.apis:
            return

        try:
            choice = int(input("\nEnter the number of the API to delete: "))
            if choice < 1 or choice > len(self.apis):
                print("Invalid selection")
                return

            api = self.apis[choice - 1]

            confirm = input(
                f"Are you sure you want to delete {api['method']} {api['path']}? (y/n): "
            )
            if confirm.lower() == "y":
                del self.apis[choice - 1]
                self.save_config()
                print("API endpoint deleted")
            else:
                print("Operation cancelled")

        except ValueError:
            print("Please enter a valid number")

    def _extract_json_from_llm_response(self, content, default_values):
        """Extract JSON from LLM response with fallback to default values"""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    pass
            print("Failed to parse LLM response as JSON. Using default values.")
            return default_values

    def run_server(self):
        """Run the API server"""
        if not self.apis:
            print(
                "No API endpoints defined. Please add some before running the server."
            )
            return

        if not self.has_database_config():
            print(
                "No database connection configured. Please set up the database first."
            )
            return

        # Find an available port starting from 8000
        port = 8000
        while True:
            try:
                # Try to bind to the port
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("localhost", port))
                s.close()
                break
            except socket.error:
                port += 1
                if port > 9000:  # Set a reasonable limit
                    print("No available ports found in range 8000-9000")
                    return

        server = HTTPServer(
            ("localhost", port), lambda *args: RequestHandler(self, *args)
        )

        print(f"\n=== Starting API Server on http://localhost:{port} ===")
        print("Press Ctrl+C to stop the server")

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped")

    def main_menu(self):
        """Display the main menu and handle user choices"""
        while True:
            print("\n=== Vibe API Server ===")

            # Check if we need database setup
            if not self.has_database_config():
                print("No database connection configured")
                if not self.setup_database():
                    print("Database setup skipped or failed.")
                    choice = input("Continue without database? (y/n): ")
                    if choice.lower() != "y":
                        print("Exiting.")
                        break

            # Show API list
            self.show_api_list()

            # Show menu options
            print("\nOptions:")
            print("1. Add an API")
            print("2. Update an API")
            print("3. Delete an API")
            print("4. Run API Server")
            print("5. Exit")

            choice = input("\nEnter your choice (1-5): ")

            if choice == "1":
                self.add_api()
            elif choice == "2":
                self.update_api()
            elif choice == "3":
                self.delete_api()
            elif choice == "4":
                self.run_server()
            elif choice == "5":
                print("Exiting API Server")
                break
            else:
                print("Invalid choice. Please enter a number between 1 and 5.")


class RequestHandler(BaseHTTPRequestHandler):
    def __init__(self, api_server, *args, **kwargs):
        self.api_server = api_server
        BaseHTTPRequestHandler.__init__(self, *args, **kwargs)

    def do_GET(self):
        self.process_request("GET")

    def do_POST(self):
        self.process_request("POST")

    def do_PUT(self):
        self.process_request("PUT")

    def do_DELETE(self):
        self.process_request("DELETE")

    def process_request(self, method):
        try:
            path = self.path.split("?")[0]

            # Special route for API documentation
            if method == "GET" and path == "/docs":
                self.send_response(200)
                self.send_header("Content-type", "text/markdown")
                self.end_headers()

                # Generate API documentation without DB info
                content = "# API Documentation\n\n"

                # Add API list summary
                if self.api_server.apis:
                    content += "## API Endpoints\n\n"
                    for api in self.api_server.apis:
                        content += f"- **{api['method']}** - {api['path']} - {api['description']}\n"
                    content += "\n"

                # Add detailed API definitions
                if self.api_server.apis:
                    content += "## API Definitions\n\n"
                    for api in self.api_server.apis:
                        content += f"### API: {api['name']}\n"
                        content += f"#### HTTP Method\n{api['method']}\n"
                        content += f"#### Path\n{api['path']}\n"
                        content += f"#### Description\n{api['description']}\n"

                self.wfile.write(content.encode())
                return

            # Find matching API
            matching_api = None
            path_params = {}

            for api in self.api_server.apis:
                if api["method"] == method:
                    is_match, params = self._path_matches(api["path"], path)
                    if is_match:
                        matching_api = api
                        path_params = params
                        break

            if not matching_api:
                self.send_response(404)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {"status": "error", "error": "API endpoint not found"}
                    ).encode()
                )
                return

            # Get request data
            request_data = {}

            # Add path parameters to request data
            if path_params:
                request_data.update(path_params)

            # Parse query parameters
            if "?" in self.path:
                query_string = urlparse(self.path).query
                # Parse each parameter, handling multiple values
                parsed_params = {}
                for key, values in parse_qs(query_string).items():
                    # If there's only one value, don't make it a list
                    if len(values) == 1:
                        # Try to convert to number if it looks like one
                        value = values[0]
                        try:
                            if value.isdigit():
                                value = int(value)
                            elif (
                                value.replace(".", "", 1).isdigit()
                                and value.count(".") == 1
                            ):
                                value = float(value)
                        except (ValueError, AttributeError):
                            pass
                        parsed_params[key] = value
                    else:
                        parsed_params[key] = values

                request_data.update(parsed_params)

            # Parse body for POST/PUT
            if method in ["POST", "PUT"]:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8")

                try:
                    # Default to JSON for all requests
                    content_type = self.headers.get("Content-Type", "").lower()
                    if "json" in content_type or body.strip().startswith("{"):
                        request_data.update(json.loads(body))
                    elif "x-www-form-urlencoded" in content_type:
                        # Handle form data if explicitly specified
                        form_data = {}
                        for pair in body.split("&"):
                            if "=" in pair:
                                key, value = pair.split("=", 1)
                                form_data[key] = value
                        request_data.update(form_data)
                    elif body.strip():
                        # Try JSON as fallback for any non-empty body
                        try:
                            request_data.update(json.loads(body))
                        except:
                            self.send_response(400)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(
                                json.dumps(
                                    {
                                        "status": "error",
                                        "error": "Invalid request format. Expected JSON",
                                    }
                                ).encode()
                            )
                            return
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.send_header("Content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps(
                            {"status": "error", "error": "Invalid JSON in request body"}
                        ).encode()
                    )
                    return

            # Process with LLM
            response = self._process_with_llm(matching_api, request_data)

            # Send response
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()

            # Ensure we have a valid JSON response
            if isinstance(response, str):
                try:
                    # Try to parse as JSON if it's a string
                    response = json.loads(response)
                except:
                    response = {"status": "success", "result": response}

            # Convert to JSON and send
            response_json = json.dumps(response, indent=2, cls=DateTimeEncoder)
            self.wfile.write(response_json.encode())

        except Exception as e:
            # Log the error
            import traceback

            print(f"ERROR: {str(e)}")
            print(traceback.format_exc())

            # Return a detailed error response for debugging
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "status": "error",
                        "message": "Internal server Error",
                        "error_detail": str(e),
                        "traceback": traceback.format_exc(),
                    },
                    indent=2,
                    cls=DateTimeEncoder,
                ).encode()
            )

    def _path_matches(self, api_path, request_path):
        """Check if the request path matches an API path pattern and extract path parameters"""
        # Convert API path pattern to regex
        # e.g., /users/{id} -> /users/([^/]+)
        pattern_parts = []
        param_names = []

        path_parts = api_path.strip("/").split("/")
        for part in path_parts:
            if part.startswith("{") and part.endswith("}"):
                # This is a path parameter
                param_name = part[1:-1]  # Remove { and }
                param_names.append(param_name)
                pattern_parts.append("([^/]+)")
            else:
                # This is a regular path segment
                pattern_parts.append(re.escape(part))

        # Build the regex pattern
        pattern = "^/" + "/".join(pattern_parts) + "$"

        # Try to match the pattern
        match = re.match(pattern, request_path)

        if match:
            # If we have path parameters, return them
            if param_names:
                return True, dict(zip(param_names, match.groups()))
            return True, {}

        return False, {}

    def _process_with_llm(self, api, request_data):
        """Process the request using LLM and database access"""
        # Prepare conversation with LLM
        messages = [
            {
                "role": "system",
                "content": f"""
            You are an API server implementing the following endpoint:
            
            Method: {api["method"]}
            Path: {api["path"]}
            Description: {api["description"]}
            
            Implementation steps:
            {api["implementation"]}
            
            Database schema information:
            {json.dumps(self.api_server.config.get("database", {}).get("schema", {}), indent=2)}
            
            When you need to query the database, you can use the 'database_query' function.
            
            IMPORTANT:
            1. Your final response MUST be valid JSON that will be returned to the API caller
            2. Strictly follow the response schema defined in the implementation steps
            3. Only include data fields that are specified in the schema
            4. Always include error handling with appropriate status codes
            5. All input and output must be in JSON format
            """,
            }
        ]

        # Add request info to the conversation
        messages.append(
            {
                "role": "user",
                "content": f"""
            Process this API request:
            
            Request data: {json.dumps(request_data, indent=2)}
            
            Respond with ONLY the appropriate JSON that should be returned to the client.
            Do not include any explanations or markdown formatting in your response.
            The response should be a valid JSON object that matches the schema specified in the implementation steps.
            """,
            }
        )

        # Chat history for multi-turn interactions
        chat_history = []
        max_turns = 10  # Limit to prevent infinite loops

        for turn in range(max_turns):
            response = self.api_server.openai_client.chat.completions.create(
                model="gpt-4",
                messages=messages + chat_history,
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "database_query",
                            "description": "Execute a SQL query against the database",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "The SQL query to execute",
                                    },
                                    "params": {
                                        "type": "array",
                                        "description": "Parameters for the SQL query",
                                        "items": {"type": "string"},
                                    },
                                    "read_only": {
                                        "type": "boolean",
                                        "description": "Whether this is a read-only query (true) or a data modification query (false)",
                                        "default": True,
                                    },
                                },
                                "required": ["query"],
                            },
                        },
                    }
                ],
            )

            message = response.choices[0].message

            if not hasattr(message, "tool_calls") or not message.tool_calls:
                # LLM is ready to provide final response
                try:
                    # Try to parse as JSON
                    result = json.loads(message.content)
                    return result
                except json.JSONDecodeError:
                    # Try to extract JSON from markdown code blocks
                    json_match = re.search(
                        r"```(?:json)?\s*(.*?)\s*```", message.content, re.DOTALL
                    )
                    if json_match:
                        try:
                            return json.loads(json_match.group(1))
                        except:
                            pass

                    # If all parsing fails, return as plain text
                    return {"status": "success", "result": message.content}

            # Process tool calls
            for tool_call in message.tool_calls:
                if tool_call.function.name == "database_query":
                    function_args = json.loads(tool_call.function.arguments)
                    query = function_args.get("query", "")
                    params = function_args.get("params", [])
                    read_only = function_args.get("read_only", True)

                    # Execute the query
                    db_result = self.api_server.execute_db_query(
                        query, params, read_only
                    )

                    # Add to chat history
                    chat_history.append(message)
                    chat_history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": "database_query",
                            "content": json.dumps(db_result, cls=DateTimeEncoder),
                        }
                    )

        # If we've reached the max turns, return an error
        return {
            "status": "error",
            "error": "Processing limit reached without generating a final response",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="API Server based on configuration file"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=CONFIG_FILE,
        help=f"Configuration file path (default: {CONFIG_FILE})",
    )
    parser.add_argument(
        "-s", "--server", action="store_true", help="Start API server immediately"
    )
    args = parser.parse_args()

    api_server = APIServer(config_file=args.config)

    if args.server:
        api_server.run_server()
    else:
        api_server.main_menu()
