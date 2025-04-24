import streamlit as st
import ollama
import os
import zipfile
import io
import re
import time
import json
import requests
import tempfile
import subprocess
import shutil
import platform
import base64
from datetime import datetime
from pygments import highlight
from pygments.lexers import JavaLexer, XmlLexer, PropertiesLexer, YamlLexer, JsonLexer
from pygments.formatters import HtmlFormatter

# Initialize session state variables
if "messages" not in st.session_state:
    st.session_state.messages = []
if "generated_files" not in st.session_state:
    st.session_state.generated_files = {}
if "test_files" not in st.session_state:
    st.session_state.test_files = {}
if "file_categories" not in st.session_state:
    st.session_state.file_categories = {
        "main": [],
        "test": [],
        "config": []
    }
if "logs" not in st.session_state:
    st.session_state.logs = []
if "project_metadata" not in st.session_state:
    st.session_state.project_metadata = {
        "app_name": "spring-boot-app",
        "group_id": "com.example",
        "artifact_id": "demo",
        "description": "Spring Boot Application",
        "java_version": "17",
        "spring_boot_version": "3.2.3",
    }
if "code_execution_result" not in st.session_state:
    st.session_state.code_execution_result = None

# Function to add log entries
def add_log(level, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp} [{level}] {message}"
    st.session_state.logs.append(log_entry)
    print(log_entry)  # Also print to console for debugging

# Function to extract code blocks from the response
def extract_code_blocks(text):
    if not text or not text.strip():
        add_log("WARNING", "No text to extract code blocks from")
        return [], []
    
    add_log("INFO", f"Extracting code blocks from text of length {len(text)}")
    
    # Pattern to match code blocks with language specification
    pattern = r"```(?:(java|xml|properties|yml|yaml|json))?\s*([\s\S]*?)```"
    matches = re.finditer(pattern, text)
    
    code_blocks = []
    languages = []
    for match in matches:
        lang = match.group(1) if match.group(1) else "text"
        code = match.group(2).strip()
        code_blocks.append(code)
        languages.append(lang)
    
    add_log("INFO", f"Found {len(code_blocks)} code blocks")
    return code_blocks, languages

# Function to detect file type based on content
def detect_file_type(content, language_hint=None):
    if language_hint in ["java", "xml", "properties", "yml", "yaml", "json"]:
        return language_hint
    
    if "public class" in content or "import org.springframework" in content:
        return "java"
    elif "<project" in content or "<dependencies" in content or "<?xml" in content:
        return "xml"
    elif "spring.datasource.url" in content or "server.port" in content:
        return "properties"
    elif "---" in content and (":" in content) and ("  " in content):
        return "yaml"
    elif content.strip().startswith("{") and content.strip().endswith("}"):
        return "json"
    else:
        return "text"

# Function to suggest filename based on content
def suggest_filename(content, file_type):
    if file_type == "java":
        # Check if it's a test file
        is_test = "import org.junit" in content or "@Test" in content
        
        class_match = re.search(r"public\s+class\s+(\w+)", content)
        if class_match:
            class_name = class_match.group(1)
            if is_test:
                return f"{class_name}.java", "test"
            else:
                return f"{class_name}.java", "main"
        else:
            if is_test:
                return "TestClass.java", "test"
            else:
                return "JavaClass.java", "main"
    
    elif file_type == "xml" and "pom" in content.lower():
        return "pom.xml", "config"
    elif file_type == "xml" and "application-context" in content.lower():
        return "application-context.xml", "config"
    elif file_type == "xml":
        return "config.xml", "config"
    elif file_type == "properties":
        if "test" in content.lower():
            return "application-test.properties", "config"
        else:
            return "application.properties", "config"
    elif file_type == "yaml" or file_type == "yml":
        if "test" in content.lower():
            return "application-test.yml", "config"
        else:
            return "application.yml", "config"
    elif file_type == "json":
        return "config.json", "config"
    else:
        return "file.txt", "config"

# Function to generate a zip file with all code files
def generate_zip_file(files_dict, include_spring_initializr=False):
    zip_buffer = io.BytesIO()
    
    # If Spring Initializr is requested, generate a base project first
    if include_spring_initializr:
        try:
            # Prepare Spring Initializr request
            initializr_url = "https://start.spring.io/starter.zip"
            params = {
                "type": "maven-project",
                "language": "java",
                "bootVersion": st.session_state.project_metadata["spring_boot_version"],
                "baseDir": st.session_state.project_metadata["app_name"],
                "groupId": st.session_state.project_metadata["group_id"],
                "artifactId": st.session_state.project_metadata["artifact_id"],
                "name": st.session_state.project_metadata["app_name"],
                "description": st.session_state.project_metadata["description"],
                "packageName": f"{st.session_state.project_metadata['group_id']}.{st.session_state.project_metadata['artifact_id']}",
                "packaging": "jar",
                "javaVersion": st.session_state.project_metadata["java_version"],
                "dependencies": "web,data-jpa,lombok,actuator"
            }
            
            add_log("INFO", "Requesting base project from Spring Initializr")
            response = requests.get(initializr_url, params=params)
            
            if response.status_code == 200:
                add_log("INFO", "Successfully got Spring Initializr template")
                
                # Extract the Spring Initializr ZIP
                with zipfile.ZipFile(io.BytesIO(response.content)) as init_zip:
                    # Create a new ZIP with the Spring Initializr files plus our generated files
                    with zipfile.ZipFile(zip_buffer, 'a', zipfile.ZIP_DEFLATED, False) as zip_file:
                        # Copy all files from Spring Initializr
                        for item in init_zip.infolist():
                            zip_file.writestr(item.filename, init_zip.read(item.filename))
                        
                        # Now add our generated files, properly organizing them
                        organized_files = organize_project_files(files_dict)
                        base_package_path = f"{st.session_state.project_metadata['group_id']}.{st.session_state.project_metadata['artifact_id']}".replace('.', '/')
                        
                        for directory, files in organized_files.items():
                            for filename, content in files.items():
                                if directory == "src/main/java" or directory == "src/test/java":
                                    # Place Java files in the correct package structure
                                    file_path = f"{directory}/{base_package_path}/{filename}"
                                    # Update package declarations in the file
                                    if detect_file_type(content) == "java":
                                        content = update_package_declaration(content, f"{st.session_state.project_metadata['group_id']}.{st.session_state.project_metadata['artifact_id']}")
                                else:
                                    file_path = f"{directory}/{filename}" if directory else filename
                                
                                # Only write the file if it doesn't exist in the Spring Initializr template
                                # or if we're intentionally overwriting it (like pom.xml)
                                try:
                                    init_zip.getinfo(file_path)
                                    if filename == "pom.xml":  # Always overwrite pom.xml with our version
                                        zip_file.writestr(file_path, content)
                                except KeyError:  # File doesn't exist in the original ZIP
                                    zip_file.writestr(file_path, content)
                
                return zip_buffer.getvalue()
            else:
                add_log("ERROR", f"Spring Initializr request failed with status code {response.status_code}")
        except Exception as e:
            add_log("ERROR", f"Failed to generate project with Spring Initializr: {str(e)}")
    
    # Fallback to regular ZIP file generation
    with zipfile.ZipFile(zip_buffer, 'a', zipfile.ZIP_DEFLATED, False) as zip_file:
        organized_files = organize_project_files(files_dict)
        for directory, files in organized_files.items():
            for filename, content in files.items():
                file_path = f"{directory}/{filename}" if directory else filename
                zip_file.writestr(file_path, content)
    
    return zip_buffer.getvalue()

# Function to update package declarations in Java files
def update_package_declaration(content, package_name):
    # Check if the file already has a package declaration
    package_match = re.search(r'^package\s+([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*);', content, re.MULTILINE)
    
    if package_match:
        # Replace existing package declaration
        return re.sub(r'^package\s+([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*);',
                     f'package {package_name};', content, 1, re.MULTILINE)
    else:
        # Add package declaration at the beginning
        return f'package {package_name};\n\n{content}'

# Function to get syntax highlighted code
def get_highlighted_code(code, file_type):
    if file_type == "java":
        lexer = JavaLexer()
    elif file_type == "xml":
        lexer = XmlLexer()
    elif file_type == "properties":
        lexer = PropertiesLexer()
    elif file_type == "yaml" or file_type == "yml":
        lexer = YamlLexer()
    elif file_type == "json":
        lexer = JsonLexer()
    else:
        # Default to Java for unknown types
        lexer = JavaLexer()
    
    formatter = HtmlFormatter(style="friendly")
    highlighted = highlight(code, lexer, formatter)
    css = formatter.get_style_defs('.highlight')
    
    return highlighted, css

# Function to test Ollama connection directly
def test_ollama_connection():
    try:
        add_log("INFO", "Testing Ollama connection...")
        response = requests.get("http://localhost:11434/api/tags", timeout=10)
        if response.status_code == 200:
            models = response.json().get("models", [])
            model_names = [model.get("name") for model in models]
            add_log("INFO", f"Ollama connection successful. Available models: {', '.join(model_names)}")
            return True, model_names
        else:
            add_log("ERROR", f"Ollama returned status code {response.status_code}")
            return False, []
    except requests.exceptions.Timeout:
        add_log("ERROR", "Ollama connection test timed out after 10 seconds")
        return False, []
    except Exception as e:
        add_log("ERROR", f"Ollama connection test failed: {str(e)}")
        return False, []

# Function to check if a specific model is loaded
def check_model_loaded(model_name):
    try:
        add_log("INFO", f"Checking if model '{model_name}' is loaded...")
        response = requests.get(f"http://localhost:11434/api/show?name={model_name}", timeout=10)
        if response.status_code == 200:
            add_log("INFO", f"Model '{model_name}' is loaded")
            return True
        add_log("WARNING", f"Model '{model_name}' may not be loaded. Status code: {response.status_code}")
        return False
    except Exception as e:
        add_log("ERROR", f"Error checking model status: {str(e)}")
        return False

# Function to send a simple test message to check model is working
def test_model(model_name):
    try:
        add_log("INFO", f"Testing model '{model_name}' with a simple message...")
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": "Hello, are you working?"}],
            "stream": False,
            "options": {"temperature": 0.1}
        }
        response = requests.post("http://localhost:11434/api/chat", json=payload, timeout=30)
        
        if response.status_code == 200:
            try:
                content = response.json().get("message", {}).get("content", "")
                if content:
                    add_log("INFO", f"Model test successful. Response: {content[:50]}...")
                    return True, content[:100] + "..." if len(content) > 100 else content
                else:
                    add_log("WARNING", "Model returned empty content")
                    return False, "Empty response"
            except Exception as e:
                add_log("ERROR", f"Error parsing model response: {str(e)}")
                return False, f"Error parsing response: {str(e)}"
        else:
            add_log("ERROR", f"Model test failed with status code {response.status_code}")
            return False, f"Failed with status code {response.status_code}"
    except requests.exceptions.Timeout:
        add_log("ERROR", f"Model test timed out after 30 seconds")
        return False, "Request timed out after 30 seconds"
    except Exception as e:
        add_log("ERROR", f"Model test failed: {str(e)}")
        return False, str(e)

# Function to generate tests for a Java file
def generate_tests(java_file_content, filename):
    # Extract class name from the file
    class_match = re.search(r"public\s+class\s+(\w+)", java_file_content)
    if not class_match:
        return None, "Couldn't identify a class name to test"
    
    class_name = class_match.group(1)
    test_class_name = f"{class_name}Test"
    
    # Check if it's a Controller, Service, or Repository
    is_controller = "@Controller" in java_file_content or "@RestController" in java_file_content
    is_service = "@Service" in java_file_content
    is_repository = "@Repository" in java_file_content
    is_entity = "@Entity" in java_file_content
    
    # Prepare system prompt based on the class type
    if is_controller:
        test_type = "MockMvc controller tests"
    elif is_service:
        test_type = "service unit tests with Mockito"
    elif is_repository:
        test_type = "repository tests with @DataJpaTest"
    elif is_entity:
        test_type = "entity class validation tests"
    else:
        test_type = "JUnit tests"
    
    system_prompt = f"""
    You are an expert Java Spring Boot test generator.
    Generate complete {test_type} for the following Java class.
    The test class should follow best practices and include meaningful assertions.
    Format the response as pure Java code without any explanations or markdown.
    """
    
    test_prompt = f"""
    Generate Spring Boot tests for this class:
    
    ```java
    {java_file_content}
    ```
    
    Requirements:
    1. Name the test class {test_class_name}
    2. Use appropriate testing libraries (JUnit 5, Mockito, etc.)
    3. Test all public methods with good coverage
    4. Include proper mocking of dependencies
    5. Follow standard test naming conventions (given/when/then)
    6. Include detailed comments explaining each test case
    """
    
    try:
        with st.spinner(f"Generating tests for {filename}..."):
            # Try direct API approach first
            try:
                add_log("INFO", "Generating tests using direct API call")
                payload = {
                    "model": st.session_state.get("model", "mistral:latest"),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": test_prompt}
                    ],
                    "stream": False,
                    "options": {"temperature": st.session_state.get("temperature", 0.7)}
                }
                
                response = requests.post(
                    "http://localhost:11434/api/chat", 
                    json=payload, 
                    timeout=60
                )
                
                if response.status_code == 200:
                    test_code = response.json().get("message", {}).get("content", "")
                    if test_code:
                        # Extract only the Java code if it's wrapped in markdown code blocks
                        if "```java" in test_code:
                            code_match = re.search(r"```java\s*([\s\S]*?)```", test_code)
                            if code_match:
                                test_code = code_match.group(1).strip()
                        return test_code, test_class_name
                    else:
                        add_log("WARNING", "Empty response when generating tests")
            except Exception as direct_e:
                add_log("WARNING", f"Direct API test generation failed: {str(direct_e)}")
            
            # Fall back to ollama library
            add_log("INFO", "Falling back to ollama library for test generation")
            response = ollama.chat(
                model=st.session_state.get("model", "mistral:latest"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": test_prompt}
                ],
                options={"temperature": st.session_state.get("temperature", 0.7)}
            )
            
            test_code = response['message']['content']
            
            # Extract only the Java code if it's wrapped in markdown code blocks
            if "```java" in test_code:
                test_code = re.search(r"```java\s*([\s\S]*?)```", test_code)
                if test_code:
                    test_code = test_code.group(1).strip()
            
            return test_code, test_class_name
    except Exception as e:
        add_log("ERROR", f"Error generating tests: {str(e)}")
        return None, f"Error generating tests: {str(e)}"

# Function to generate integration tests for a REST API
def generate_integration_tests():
    # Create a prompt for generating comprehensive integration tests
    files_content = ""
    for filename, content in st.session_state.generated_files.items():
        if filename.endswith(".java"):
            files_content += f"\n\n{filename}:\n```java\n{content}\n```"
    
    system_prompt = """
    You are an expert Spring Boot integration test generator.
    Generate a comprehensive integration test class that tests the REST APIs defined in the provided files.
    The test should use MockMvc, @SpringBootTest, and include HTTP requests to test endpoints.
    Format the response as pure Java code without any explanations or markdown.
    """
    
    integration_test_prompt = f"""
    Generate Spring Boot integration tests for the following files:
    {files_content}
    
    Requirements:
    1. Name the test class ApplicationIntegrationTest
    2. Use @SpringBootTest and TestRestTemplate or WebTestClient
    3. Test all REST API endpoints
    4. Include tests for success, validation and error conditions
    5. Add appropriate assertions for response status and body
    6. Include detailed comments explaining the test setup and assertions
    """
    
    try:
        with st.spinner("Generating integration tests..."):
            try:
                add_log("INFO", "Generating integration tests using direct API call")
                payload = {
                    "model": st.session_state.get("model", "mistral:latest"),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": integration_test_prompt}
                    ],
                    "stream": False,
                    "options": {"temperature": st.session_state.get("temperature", 0.7)}
                }
                
                response = requests.post(
                    "http://localhost:11434/api/chat", 
                    json=payload, 
                    timeout=120
                )
                
                if response.status_code == 200:
                    test_code = response.json().get("message", {}).get("content", "")
                    if test_code:
                        # Extract only the Java code if it's wrapped in markdown code blocks
                        if "```java" in test_code:
                            code_match = re.search(r"```java\s*([\s\S]*?)```", test_code)
                            if code_match:
                                test_code = code_match.group(1).strip()
                        return test_code, "ApplicationIntegrationTest"
                    else:
                        add_log("WARNING", "Empty response when generating integration tests")
            except Exception as direct_e:
                add_log("WARNING", f"Direct API integration test generation failed: {str(direct_e)}")
            
            # Fall back to ollama library
            add_log("INFO", "Falling back to ollama library for integration test generation")
            response = ollama.chat(
                model=st.session_state.get("model", "mistral:latest"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": integration_test_prompt}
                ],
                options={"temperature": st.session_state.get("temperature", 0.7)}
            )
            
            test_code = response['message']['content']
            
            # Extract only the Java code if it's wrapped in markdown code blocks
            if "```java" in test_code:
                test_code = re.search(r"```java\s*([\s\S]*?)```", test_code)
                if test_code:
                    test_code = test_code.group(1).strip()
            
            return test_code, "ApplicationIntegrationTest"
    except Exception as e:
        add_log("ERROR", f"Error generating integration tests: {str(e)}")
        return None, f"Error generating integration tests: {str(e)}"

# Function to generate documentation for a Spring Boot project
def generate_documentation():
    # Collect all generated files for the documentation
    files_content = ""
    for filename, content in st.session_state.generated_files.items():
        files_content += f"\n\n{filename}:\n```{detect_file_type(content)}\n{content}\n```"
    
    system_prompt = """
    You are an expert Spring Boot developer and technical writer.
    Generate comprehensive documentation for the provided Spring Boot project.
    The documentation should include:
    1. Overview of the project architecture
    2. API documentation for all REST endpoints
    3. Description of key components and their relationships
    4. Setup and configuration instructions
    5. Examples of API usage with curl commands
    
    Format the response in clean, well-structured Markdown.
    """
    
    documentation_prompt = f"""
    Create comprehensive documentation for this Spring Boot project:
    {files_content}
    
    Project Details:
    - Name: {st.session_state.project_metadata['app_name']}
    - Group ID: {st.session_state.project_metadata['group_id']}
    - Artifact ID: {st.session_state.project_metadata['artifact_id']}
    - Description: {st.session_state.project_metadata['description']}
    - Java Version: {st.session_state.project_metadata['java_version']}
    - Spring Boot Version: {st.session_state.project_metadata['spring_boot_version']}
    
    Include:
    1. Project overview and architecture diagram (described in text)
    2. API documentation with endpoints, methods, request/response examples
    3. Database schema description (if applicable)
    4. Setup and configuration guide
    5. Sample curl commands for testing APIs
    """
    
    try:
        with st.spinner("Generating project documentation..."):
            try:
                add_log("INFO", "Generating documentation using direct API call")
                payload = {
                    "model": st.session_state.get("model", "mistral:latest"),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": documentation_prompt}
                    ],
                    "stream": False,
                    "options": {"temperature": st.session_state.get("temperature", 0.7)}
                }
                
                response = requests.post(
                    "http://localhost:11434/api/chat", 
                    json=payload, 
                    timeout=120
                )
                
                if response.status_code == 200:
                    documentation = response.json().get("message", {}).get("content", "")
                    if documentation:
                        return documentation
                    else:
                        add_log("WARNING", "Empty response when generating documentation")
            except Exception as direct_e:
                add_log("WARNING", f"Direct API documentation generation failed: {str(direct_e)}")
            
            # Fall back to ollama library
            add_log("INFO", "Falling back to ollama library for documentation generation")
            response = ollama.chat(
                model=st.session_state.get("model", "mistral:latest"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": documentation_prompt}
                ],
                options={"temperature": st.session_state.get("temperature", 0.7)}
            )
            
            documentation = response['message']['content']
            return documentation
    except Exception as e:
        add_log("ERROR", f"Error generating documentation: {str(e)}")
        return f"Error generating documentation: {str(e)}"

# Function to organize files in a project structure
def organize_project_files(files):
    project_structure = {
        "src/main/java": {},
        "src/main/resources": {},
        "src/test/java": {},
        "src/test/resources": {},
        "": {}  # Root directory
    }
    
    for filename, content in files.items():
        file_type = detect_file_type(content)
        
        if file_type == "java" and ("@Test" in content or "import org.junit" in content):
            project_structure["src/test/java"][filename] = content
        elif file_type == "java":
            project_structure["src/main/java"][filename] = content
        elif file_type in ["properties", "yml", "yaml", "json"] and "test" in filename.lower():
            project_structure["src/test/resources"][filename] = content
        elif file_type in ["properties", "yml", "yaml", "json"]:
            project_structure["src/main/resources"][filename] = content
        elif filename == "pom.xml":
            project_structure[""][filename] = content
        elif filename.lower() == "readme.md":
            project_structure[""][filename] = content
        elif filename.lower() == "dockerfile":
            project_structure[""][filename] = content
        else:
            project_structure[""][filename] = content
    
    return project_structure

# Function to generate Docker files for the project
def generate_docker_files():
    system_prompt = """
    You are an expert in containerization for Spring Boot applications.
    Generate a Dockerfile and docker-compose.yml file for a Spring Boot application.
    The Dockerfile should follow best practices for Java applications.
    Include multi-stage build for optimized container size.
    The docker-compose.yml should include the application and any necessary services.
    """
    
    docker_prompt = f"""
    Create a Dockerfile and docker-compose.yml for this Spring Boot project:
    
    Project Details:
    - Name: {st.session_state.project_metadata['app_name']}
    - Java Version: {st.session_state.project_metadata['java_version']}
    - Spring Boot Version: {st.session_state.project_metadata['spring_boot_version']}
    
    The Docker setup should:
    1. Use multi-stage build for optimization
    2. Include appropriate JVM tuning options
    3. Set up the application with proper security practices
    4. Include any necessary databases or services based on the application
    5. Configure health checks and proper networking
    
    The application uses Spring Boot {st.session_state.project_metadata['spring_boot_version']} and Java {st.session_state.project_metadata['java_version']}.
    """
    
    try:
        with st.spinner("Generating Docker configuration..."):
            try:
                add_log("INFO", "Generating Docker files using direct API call")
                payload = {
                    "model": st.session_state.get("model", "mistral:latest"),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": docker_prompt}
                    ],
                    "stream": False,
                    "options": {"temperature": st.session_state.get("temperature", 0.7)}
                }
                
                response = requests.post(
                    "http://localhost:11434/api/chat", 
                    json=payload, 
                    timeout=60
                )
                
                if response.status_code == 200:
                    docker_response = response.json().get("message", {}).get("content", "")
                    if docker_response:
                        # Extract Dockerfile and docker-compose.yml
                        dockerfile_match = re.search(r"```dockerfile\s*([\s\S]*?)```", docker_response)
                        compose_match = re.search(r"```(yaml|yml)\s*([\s\S]*?)```", docker_response)
                        
                        dockerfile = dockerfile_match.group(1).strip() if dockerfile_match else ""
                        docker_compose = compose_match.group(2).strip() if compose_match else ""
                        
                        return dockerfile, docker_compose
                    else:
                        add_log("WARNING", "Empty response when generating Docker files")
            except Exception as direct_e:
                add_log("WARNING", f"Direct API Docker files generation failed: {str(direct_e)}")
            
            # Fall back to ollama library
            add_log("INFO", "Falling back to ollama library for Docker files generation")
            response = ollama.chat(
                model=st.session_state.get("model", "mistral:latest"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": docker_prompt}
                ],
                options={"temperature": st.session_state.get("temperature", 0.7)}
            )
            
            docker_response = response['message']['content']
            
            # Extract Dockerfile and docker-compose.yml
            dockerfile_match = re.search(r"```dockerfile\s*([\s\S]*?)```", docker_response)
            compose_match = re.search(r"```(yaml|yml)\s*([\s\S]*?)```", docker_response)
            
            dockerfile = dockerfile_match.group(1).strip() if dockerfile_match else ""
            docker_compose = compose_match.group(2).strip() if compose_match else ""
            
            return dockerfile, docker_compose
    except Exception as e:
        add_log("ERROR", f"Error generating Docker files: {str(e)}")
        return None, f"Error generating Docker files: {str(e)}"

# Function to run the Spring Boot project locally (simplified for demo)
def run_project_locally():
    result = {"success": False, "message": "", "output": ""}
    
    try:
        # Create a temporary directory
        with tempfile.TemporaryDirectory() as temp_dir:
            add_log("INFO", f"Created temporary directory: {temp_dir}")
            
            # Generate ZIP file with all project files
            all_files = {**st.session_state.generated_files, **st.session_state.test_files}
            zip_data = generate_zip_file(all_files, include_spring_initializr=True)
            
            # Extract ZIP to temporary directory
            with io.BytesIO(zip_data) as zip_buffer:
                with zipfile.ZipFile(zip_buffer) as zip_file:
                    zip_file.extractall(temp_dir)
            
            add_log("INFO", "Extracted project files to temporary directory")
            
            # Check if Maven or Gradle is installed
            maven_command = "mvn" if platform.system() != "Windows" else "mvn.cmd"
            
            try:
                # Run Maven commands
                add_log("INFO", "Attempting to build the project with Maven")
                
                # Change to project directory
                project_dir = os.path.join(temp_dir, st.session_state.project_metadata["app_name"])
                if not os.path.exists(project_dir):
                    project_dir = temp_dir  # Fallback if the app_name directory doesn't exist
                
                add_log("INFO", f"Using project directory: {project_dir}")
                
                # Compile project
                compile_cmd = [maven_command, "clean", "package", "-DskipTests"]
                add_log("INFO", f"Running Maven command: {' '.join(compile_cmd)}")
                
                process = subprocess.Popen(
                    compile_cmd,
                    cwd=project_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                
                stdout, stderr = process.communicate(timeout=300)  # 5 minute timeout
                
                if process.returncode != 0:
                    add_log("ERROR", f"Maven build failed: {stderr}")
                    return {"success": False, "message": "Build failed", "output": stderr}
                
                add_log("INFO", "Maven build successful")
                
                # Find the generated JAR file
                target_dir = os.path.join(project_dir, "target")
                jar_files = [f for f in os.listdir(target_dir) if f.endswith(".jar") and not f.endswith("-sources.jar")]
                
                if not jar_files:
                    add_log("ERROR", "No JAR file found after build")
                    return {"success": False, "message": "No JAR file found after build", "output": stdout}
                
                jar_file = os.path.join(target_dir, jar_files[0])
                add_log("INFO", f"Found JAR file: {jar_file}")
                
                # Run the application
                run_cmd = ["java", "-jar", jar_file]
                add_log("INFO", f"Running command: {' '.join(run_cmd)}")
                
                # Instead of actually running it (which would block the Streamlit app),
                # we'll just return success for demonstration purposes
                return {
                    "success": True, 
                    "message": "Project built successfully!",
                    "output": f"Build Output:\n{stdout}\n\nTo run the application:\njava -jar {jar_files[0]}"
                }
                
            except Exception as e:
                add_log("ERROR", f"Error building or running project: {str(e)}")
                return {"success": False, "message": f"Error: {str(e)}", "output": ""}
    
    except Exception as e:
        add_log("ERROR", f"Error setting up project directory: {str(e)}")
        return {"success": False, "message": f"Error setting up project: {str(e)}", "output": ""}

# Function for generating an OpenAPI specification
def generate_openapi_spec():
    # Collect all controller files
    controller_files = {}
    for filename, content in st.session_state.generated_files.items():
        if filename.endswith(".java") and ("@RestController" in content or "@Controller" in content):
            controller_files[filename] = content
    
    if not controller_files:
        return "No controller files found in the project"
    
    system_prompt = """
    You are an expert in OpenAPI specification generation.
    Create a complete OpenAPI 3.0 specification for the Spring Boot REST controllers provided.
    The specification should include all endpoints, request/response schemas, and proper documentation.
    Format the response as a YAML OpenAPI specification.
    """
    
    # Create a prompt with all controller files
    controllers_content = ""
    for filename, content in controller_files.items():
        controllers_content += f"\n\n{filename}:\n```java\n{content}\n```"
    
    openapi_prompt = f"""
    Generate an OpenAPI 3.0 specification for the following Spring Boot REST controllers:
    {controllers_content}
    
    Project Details:
    - Name: {st.session_state.project_metadata['app_name']}
    - Description: {st.session_state.project_metadata['description']}
    - Version: 1.0.0
    
    Requirements:
    1. Include all REST endpoints with proper paths, methods, and parameters
    2. Define request and response schemas based on the Java objects used
    3. Add detailed descriptions for all operations and schemas
    4. Include example values where appropriate
    5. Format as a valid OpenAPI 3.0 YAML specification
    """
    
    try:
        with st.spinner("Generating OpenAPI specification..."):
            try:
                add_log("INFO", "Generating OpenAPI specification using direct API call")
                payload = {
                    "model": st.session_state.get("model", "mistral:latest"),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": openapi_prompt}
                    ],
                    "stream": False,
                    "options": {"temperature": st.session_state.get("temperature", 0.7)}
                }
                
                response = requests.post(
                    "http://localhost:11434/api/chat", 
                    json=payload, 
                    timeout=60
                )
                
                if response.status_code == 200:
                    openapi_spec = response.json().get("message", {}).get("content", "")
                    if openapi_spec:
                        # Extract the YAML content if wrapped in code blocks
                        yaml_match = re.search(r"```(yaml|yml)\s*([\s\S]*?)```", openapi_spec)
                        if yaml_match:
                            openapi_spec = yaml_match.group(2).strip()
                        return openapi_spec
                    else:
                        add_log("WARNING", "Empty response when generating OpenAPI specification")
            except Exception as direct_e:
                add_log("WARNING", f"Direct API OpenAPI generation failed: {str(direct_e)}")
            
            # Fall back to ollama library
            add_log("INFO", "Falling back to ollama library for OpenAPI generation")
            response = ollama.chat(
                model=st.session_state.get("model", "mistral:latest"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": openapi_prompt}
                ],
                options={"temperature": st.session_state.get("temperature", 0.7)}
            )
            
            openapi_spec = response['message']['content']
            
            # Extract the YAML content if wrapped in code blocks
            yaml_match = re.search(r"```(yaml|yml)\s*([\s\S]*?)```", openapi_spec)
            if yaml_match:
                openapi_spec = yaml_match.group(2).strip()
            
            return openapi_spec
    except Exception as e:
        add_log("ERROR", f"Error generating OpenAPI specification: {str(e)}")
        return f"Error generating OpenAPI specification: {str(e)}"

# Function to generate GitHub Actions workflow for CI/CD
def generate_github_actions():
    system_prompt = """
    You are an expert in CI/CD for Java Spring Boot applications.
    Create a complete GitHub Actions workflow file for building, testing, and deploying a Spring Boot application.
    The workflow should include proper caching, testing, and deployment steps.
    """
    
    github_actions_prompt = f"""
    Generate a GitHub Actions workflow file for this Spring Boot project:
    
    Project Details:
    - Name: {st.session_state.project_metadata['app_name']}
    - Java Version: {st.session_state.project_metadata['java_version']}
    - Spring Boot Version: {st.session_state.project_metadata['spring_boot_version']}
    - Build Tool: Maven
    
    Requirements:
    1. Create a workflow that builds and tests the application on push to main and pull requests
    2. Include proper Java setup with caching for Maven dependencies
    3. Run unit and integration tests
    4. Build and publish a Docker image
    5. Add a deployment step (to a staging environment)
    6. Include security scanning for vulnerabilities
    7. Format as a YAML file for .github/workflows/ci-cd.yml
    """
    
    try:
        with st.spinner("Generating GitHub Actions workflow..."):
            try:
                add_log("INFO", "Generating GitHub Actions workflow using direct API call")
                payload = {
                    "model": st.session_state.get("model", "mistral:latest"),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": github_actions_prompt}
                    ],
                    "stream": False,
                    "options": {"temperature": st.session_state.get("temperature", 0.7)}
                }
                
                response = requests.post(
                    "http://localhost:11434/api/chat", 
                    json=payload, 
                    timeout=60
                )
                
                if response.status_code == 200:
                    workflow = response.json().get("message", {}).get("content", "")
                    if workflow:
                        # Extract the YAML content if wrapped in code blocks
                        yaml_match = re.search(r"```(yaml|yml)\s*([\s\S]*?)```", workflow)
                        if yaml_match:
                            workflow = yaml_match.group(2).strip()
                        return workflow
                    else:
                        add_log("WARNING", "Empty response when generating GitHub Actions workflow")
            except Exception as direct_e:
                add_log("WARNING", f"Direct API GitHub Actions workflow generation failed: {str(direct_e)}")
            
            # Fall back to ollama library
            add_log("INFO", "Falling back to ollama library for GitHub Actions workflow generation")
            response = ollama.chat(
                model=st.session_state.get("model", "mistral:latest"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": github_actions_prompt}
                ],
                options={"temperature": st.session_state.get("temperature", 0.7)}
            )
            
            workflow = response['message']['content']
            
            # Extract the YAML content if wrapped in code blocks
            yaml_match = re.search(r"```(yaml|yml)\s*([\s\S]*?)```", workflow)
            if yaml_match:
                workflow = yaml_match.group(2).strip()
            
            return workflow
    except Exception as e:
        add_log("ERROR", f"Error generating GitHub Actions workflow: {str(e)}")
        return f"Error generating GitHub Actions workflow: {str(e)}"

# Set up the Streamlit UI
st.set_page_config(page_title="Java Spring Boot Developer Chatbot", page_icon="🤖", layout="wide")

# Custom CSS for enhanced UI
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        color: #3366ff;
        margin-bottom: 0;
    }
    .sub-header {
        font-size: 1.1rem;
        color: #666;
        margin-bottom: 2rem;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 20px;
        background-color: #f0f2f6;
        border-radius: 4px 4px 0 0;
    }
    .stTabs [aria-selected="true"] {
        background-color: #3366ff !important;
        color: white !important;
    }
    .feature-card {
        background-color: #f8f9fa;
        padding: 20px;
        border-radius: 10px;
        border: 1px solid #eee;
        margin-bottom: 20px;
    }
    .feature-title {
        color: #3366ff;
        font-size: 1.2rem;
        margin-bottom: 10px;
    }
    .chat-message {
        padding: 1.5rem;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
        display: flex;
        background-color: #f8f9fa;
    }
    .file-card {
        border: 1px solid #ddd;
        border-radius: 5px;
        padding: 10px;
        margin-bottom: 10px;
    }
    .file-header {
        display: flex;
        justify-content: space-between;
        border-bottom: 1px solid #eee;
        padding-bottom: 5px;
        margin-bottom: 5px;
    }
    .file-type-java {
        color: #b07219;
    }
    .file-type-xml {
        color: #e34c26;
    }
    .file-type-properties {
        color: #89e051;
    }
    .file-type-yaml {
        color: #cb171e;
    }
    .btn-primary {
        background-color: #3366ff;
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 4px;
        cursor: pointer;
    }
    .btn-secondary {
        background-color: #6c757d;
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 4px;
        cursor: pointer;
    }
</style>
""", unsafe_allow_html=True)

# Header section
col1, col2 = st.columns([3, 1])
with col1:
    st.markdown('<h1 class="main-header">🤖 Java Spring Boot Developer Assistant</h1>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Generate Spring Boot code, tests, documentation, and more with AI assistance</p>', unsafe_allow_html=True)

# Main app layout
tab1, tab2, tab3, tab4, tab5 = st.tabs(["💬 Chat", "📁 Project Files", "🧪 Testing", "🚀 Deployment", "📚 Documentation"])

with tab1:  # Chat Tab
    # Sidebar for model configuration and file management
    with st.sidebar:
        st.header("🛠️ Configuration")
        
        # Collapsible configuration section
        with st.expander("Model Settings", expanded=True):
            # Test Ollama connection
            if st.button("Test Ollama Connection"):
                success, models = test_ollama_connection()
                if success:
                    st.success(f"Connection successful! Available models: {', '.join(models)}")
                else:
                    st.error("Failed to connect to Ollama. Check logs for details.")
            
            model = st.selectbox(
                "Select Model", 
                ["mistral:latest", "deepseek-r1:latest", "llama3.1:latest", "codellama:latest", "deepseek-coder:latest"], 
                index=0,
                key="model"
            )
            
            # Check if model is loaded
            if st.button("Check Model Status"):
                if check_model_loaded(model):
                    st.success(f"Model '{model}' is loaded!")
                else:
                    st.error(f"Model '{model}' may not be loaded. Try running: ollama pull {model}")
            
            # Test model with simple message
            if st.button("Test Model"):
                success, response = test_model(model)
                if success:
                    st.success(f"Model is working! Sample response: {response}")
                else:
                    st.error(f"Model test failed: {response}")
            
            temperature = st.slider(
                "Temperature", 
                min_value=0.1, 
                max_value=1.0, 
                value=0.7, 
                step=0.1,
                key="temperature"
            )
        
        # Project metadata
        with st.expander("Project Settings", expanded=True):
            st.session_state.project_metadata["app_name"] = st.text_input(
                "Application Name",
                value=st.session_state.project_metadata["app_name"]
            )
            st.session_state.project_metadata["group_id"] = st.text_input(
                "Group ID",
                value=st.session_state.project_metadata["group_id"]
            )
            st.session_state.project_metadata["artifact_id"] = st.text_input(
                "Artifact ID",
                value=st.session_state.project_metadata["artifact_id"]
            )
            st.session_state.project_metadata["description"] = st.text_area(
                "Description",
                value=st.session_state.project_metadata["description"]
            )
            st.session_state.project_metadata["java_version"] = st.selectbox(
                "Java Version",
                ["8", "11", "17", "21"],
                index=2,  # Default to Java 17
                key="java_version"
            )
            st.session_state.project_metadata["spring_boot_version"] = st.selectbox(
                "Spring Boot Version",
                ["2.7.18", "3.0.12", "3.1.9", "3.2.3"],
                index=3,  # Default to latest
                key="spring_boot_version"
            )
        
        # Debug logs expander
        with st.expander("Debug Logs"):
            if st.button("Clear Logs"):
                st.session_state.logs = []
            
            # Display the last 20 logs
            st.code("\n".join(st.session_state.logs[-20:]), language="text")
        
        st.header("🧠 Quick Prompts")
        default_quick_prompts = [
            "Create a Spring Boot REST API for a blog with posts and comments",
            "Show me how to implement JWT authentication with Spring Security",
            "Generate a Spring Boot application with Spring Data JPA and PostgreSQL",
            "Create a microservice for user management with validation",
            "Build a Spring WebFlux reactive REST API",
            "Generate a simple Spring Boot CRUD API with Swagger documentation",
            "Create a Spring Boot application with Redis caching",
            "Show me how to implement rate limiting in Spring Boot",
            "Build a file upload/download service with Spring Boot",
            "Create a Spring Boot application with Kafka integration"
        ]
        
        # Database relationship prompts
        db_relationship_prompts = [
            "Create a Spring Boot entity model with One-to-One relationship between User and UserProfile",
            "Generate entities with One-to-Many relationship between Department and Employee",
            "Implement Many-to-Many relationship between Student and Course with JPA",
            "Create a bidirectional One-to-Many relationship between Order and OrderItem entities",
            "Generate a self-referencing entity relationship for an Employee hierarchy"
        ]

        # MVC structure prompts
        mvc_prompts = [
            "Generate a complete controller-service-repository structure for a Product entity",
            "Create a REST controller with CRUD operations for a Customer entity",
            "Implement a service layer with business logic for Order processing",
            "Build a repository with custom query methods for advanced data filtering",
            "Create a complete MVC structure with DTO pattern and mappers"
        ]

        # Database prompts
        database_prompts = [
            "Configure Spring Boot with MySQL database and connection pooling",
            "Set up PostgreSQL with Spring Boot including migrations with Flyway",
            "Implement MongoDB repositories in Spring Boot for a Document entity",
            "Configure multiple datasources in a Spring Boot application",
            "Set up an in-memory H2 database for testing with Spring Boot"
        ]

        # Add a way to manage custom prompts
        if "custom_prompts" not in st.session_state:
            st.session_state.custom_prompts = []

        # Allow users to add/edit custom prompts
        with st.expander("Manage Custom Prompts"):
            new_prompt = st.text_area("New custom prompt:", height=100, 
                                    placeholder="Enter a new custom prompt here...")
            if st.button("Add Custom Prompt") and new_prompt.strip():
                st.session_state.custom_prompts.append(new_prompt.strip())
                st.success(f"Added new prompt: {new_prompt.strip()}")
            
            if st.session_state.custom_prompts:
                st.subheader("Your Custom Prompts")
                for i, prompt in enumerate(st.session_state.custom_prompts):
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.text(f"{i+1}. {prompt}")
                    with col2:
                        if st.button("Delete", key=f"delete_prompt_{i}"):
                            st.session_state.custom_prompts.pop(i)
                            st.rerun()

            # Select prompt category
            prompt_category = st.radio(
                "Prompt Category:",
                ["General", "Database Relationships", "MVC Structure", "Database Config", "Custom"],
                horizontal=True
            )

            # Show the appropriate prompt list based on selection
            if prompt_category == "General":
                selected_prompt = st.selectbox("Select a prompt", [""] + default_quick_prompts)
            elif prompt_category == "Database Relationships":
                selected_prompt = st.selectbox("Select a prompt", [""] + db_relationship_prompts)
            elif prompt_category == "MVC Structure":
                selected_prompt = st.selectbox("Select a prompt", [""] + mvc_prompts)
            elif prompt_category == "Database Config":
                selected_prompt = st.selectbox("Select a prompt", [""] + database_prompts)
            else:  # Custom
                if st.session_state.custom_prompts:
                    selected_prompt = st.selectbox("Select a prompt", [""] + st.session_state.custom_prompts)
                else:
                    st.info("You haven't added any custom prompts yet. Add them in the 'Manage Custom Prompts' section above.")
                    selected_prompt = ""

            if selected_prompt:
                edited_prompt = st.text_area("Edit prompt before executing:", 
                             value=selected_prompt,
                             height=100)
    
                col1, col2 = st.columns([1, 4])
                with col1:
                    if st.button("Run Prompt"):
                        st.session_state.quick_prompt = edited_prompt
                with col2:
                    if st.button("Save as Custom"):
                        if edited_prompt != selected_prompt and edited_prompt.strip():
                            if edited_prompt not in st.session_state.custom_prompts:
                                st.session_state.custom_prompts.append(edited_prompt)
                                st.success("Saved to custom prompts!")
                            else:
                                st.info("This prompt already exists in your custom prompts.")
        
        
      

    # Display chat history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Use quick prompt if selected
    prompt = st.chat_input("Ask me about Spring Boot development...")
    if "quick_prompt" in st.session_state and st.session_state.quick_prompt:
        prompt = st.session_state.quick_prompt
        st.session_state.quick_prompt = None

    # Chat input processing
    if prompt:
        # Add user message to chat history
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        
        # Display assistant response in chat message container
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            full_response = ""
            
            system_prompt = """
            You are an expert Java Spring Boot developer assistant.
            Your task is to help developers by generating Java Spring Boot code examples, explaining concepts, and answering questions.
            When generating code, make sure it's complete, well-commented, and follows best practices.
            For larger applications, organize your response to show the file structure and explain how the components work together.
            Always provide complete file contents rather than snippets.
            When generating code with multiple files, ensure the names are consistent across files (package names, class names, etc.)
            Use the latest Spring Boot conventions and practices.
            """
            
            try:
                # Prepare message payload
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ]
                
                # Process previous messages for context (up to 5 messages)
                context_count = min(5, len(st.session_state.messages) - 1)
                for i in range(len(st.session_state.messages) - context_count - 1, len(st.session_state.messages) - 1):
                    if i >= 0:
                        messages.insert(1, {
                            "role": st.session_state.messages[i]["role"],
                            "content": st.session_state.messages[i]["content"]
                        })
                
                add_log("INFO", f"Sending request to Ollama with model: {model}")
                
                # First try direct API call (non-streaming) as a test
                try:
                    add_log("INFO", "Testing direct API call...")
                    payload = {
                        "model": model,
                        "messages": messages,
                        "stream": False,
                        "options": {"temperature": temperature}
                    }
                    
                    with st.spinner("Checking Ollama..."):
                        direct_response = requests.post(
                            "http://localhost:11434/api/chat", 
                            json=payload, 
                            timeout=10  # Short timeout just to check connection
                        )
                        
                        add_log("INFO", f"Direct API call response code: {direct_response.status_code}")
                        if direct_response.status_code == 200:
                            add_log("INFO", "Direct API call test successful")
                        else:
                            add_log("WARNING", f"Direct API call test failed with status: {direct_response.status_code}")
                except Exception as direct_e:
                    add_log("WARNING", f"Direct API call test failed: {str(direct_e)}")
                
                # Now proceed with streaming response using enhanced method
                with st.spinner("Generating response..."):
                    try:
                        # Try first with Python library
                        add_log("INFO", "Trying ollama Python library with streaming...")
                        
                        try:
                            # Stream the response with ollama library
                            response = ollama.chat(
                                model=model,
                                messages=messages,
                                stream=True,
                                options={"temperature": temperature}
                            )
                            
                            for chunk in response:
                                if "content" in chunk and chunk["content"]:
                                    add_log("DEBUG", f"Received chunk: {len(chunk['content'])} chars")
                                    full_response += chunk["content"]
                                    message_placeholder.markdown(full_response + "▌")
                                    time.sleep(0.01)
                                    
                            add_log("INFO", f"Ollama library streaming complete. Length: {len(full_response)}")
                            
                        except Exception as lib_e:
                            add_log("WARNING", f"Ollama library streaming failed: {str(lib_e)}")
                            
                            # Fall back to direct API streaming if library failed
                            if not full_response.strip():
                                add_log("INFO", "Falling back to direct API streaming...")
                                
                                stream_payload = {
                                    "model": model,
                                    "messages": messages,
                                    "stream": True,
                                    "options": {"temperature": temperature}
                                }
                                
                                with requests.post(
                                    "http://localhost:11434/api/chat", 
                                    json=stream_payload, 
                                    stream=True, 
                                    timeout=120
                                ) as stream_response:
                                    add_log("INFO", f"Stream API call response code: {stream_response.status_code}")
                                    
                                    if stream_response.status_code == 200:
                                        add_log("INFO", "Direct API stream started successfully")
                                        empty_chunk_count = 0
                                        
                                        for line in stream_response.iter_lines():
                                            if line:
                                                try:
                                                    data = json.loads(line)
                                                    if "message" in data and "content" in data["message"]:
                                                        chunk_content = data["message"]["content"]
                                                        if chunk_content:
                                                            add_log("DEBUG", f"Received chunk: {len(chunk_content)} chars")
                                                            full_response += chunk_content
                                                            message_placeholder.markdown(full_response + "▌")
                                                            empty_chunk_count = 0
                                                        else:
                                                            empty_chunk_count += 1
                                                    
                                                    # Check for done message
                                                    if data.get("done", False):
                                                        add_log("INFO", "Stream completed (done=true)")
                                                        break
                                                except json.JSONDecodeError:
                                                    pass
                                            else:
                                                empty_chunk_count += 1
                                            
                                            # Break if too many empty chunks
                                            if empty_chunk_count > 50:
                                                add_log("WARNING", "Too many empty chunks, stopping stream")
                                                break
                                    else:
                                        add_log("ERROR", f"Stream API call failed with status: {stream_response.status_code}")
                                
                        # Last resort: try non-streaming if we still have no content
                        if not full_response.strip():
                            add_log("INFO", "Falling back to non-streaming API call...")
                            non_stream_payload = {
                                "model": model,
                                "messages": messages,
                                "stream": False,
                                "options": {"temperature": temperature}
                            }
                            
                            fallback_response = requests.post(
                                "http://localhost:11434/api/chat", 
                                json=non_stream_payload, 
                                timeout=500
                            )
                            
                            if fallback_response.status_code == 200:
                                full_response = fallback_response.json().get("message", {}).get("content", "")
                                add_log("INFO", f"Non-streaming fallback successful. Length: {len(full_response)}")
                            else:
                                add_log("ERROR", f"Non-streaming fallback failed: {fallback_response.status_code}")
                        
                    except Exception as e:
                        add_log("ERROR", f"Error during response generation: {str(e)}")
                        message_placeholder.error(f"Error: {str(e)}")
                
                # Check if we got a response
                if not full_response.strip():
                    add_log("ERROR", "Received empty response from Ollama")
                    message_placeholder.error("Received empty response. Check if Ollama is running and model is loaded.")
                    
                    # Show troubleshooting info if no response
                    st.error("""
                    No response from Ollama. Try these troubleshooting steps:
                    
                    1. Check if Ollama is running with `ollama serve`
                    2. Make sure you've pulled the model with `ollama pull mistral`
                    3. Use the "Test Ollama Connection" and "Check Model Status" buttons in the sidebar
                    4. Try restarting both Ollama and this Streamlit app
                    """)
                else:
                    # Final response display
                    add_log("INFO", f"Final response complete. Length: {len(full_response)}")
                    message_placeholder.markdown(full_response)
                    
                    # Extract and process code blocks from the response
                    code_blocks, languages = extract_code_blocks(full_response)
                    
                    if code_blocks:
                        st.write("---")
                        st.subheader("Generated Code Files")
                        
                        tabs = []
                        file_info = []
                        
                        for i, code in enumerate(code_blocks):
                            file_type = detect_file_type(code, languages[i] if i < len(languages) else None)
                            filename, category = suggest_filename(code, file_type)
                            
                            # Ensure unique filenames
                            base_name = filename.split('.')[0]
                            extension = filename.split('.')[-1]
                            counter = 1
                            original_filename = filename
                            while filename in st.session_state.generated_files and st.session_state.generated_files[filename] != code:
                                filename = f"{base_name}_{counter}.{extension}"
                                counter += 1
                            
                            st.session_state.generated_files[filename] = code
                            
                            # Add to appropriate category list if not already there
                            if filename not in st.session_state.file_categories[category]:
                                st.session_state.file_categories[category].append(filename)
                            
                            tabs.append(filename)
                            file_info.append({
                                "filename": filename,
                                "type": file_type,
                                "category": category,
                                "original_name": original_filename
                            })
                        
                        # Display code in tabs
                        if tabs:
                            tab_objects = st.tabs(tabs)
                            for i, tab in enumerate(tab_objects):
                                with tab:
                                    code = code_blocks[i]
                                    file_type = file_info[i]["type"]
                                    filename = file_info[i]["filename"]
                                    category = file_info[i]["category"]
                                    
                                    highlighted_code, css = get_highlighted_code(code, file_type)
                                    
                                    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                                    st.markdown(highlighted_code, unsafe_allow_html=True)
                                    
                                    col1, col2, col3 = st.columns([1, 1, 1])
                                    with col1:
                                        st.download_button(
                                            label=f"Download {filename}",
                                            data=code,
                                            file_name=filename,
                                            mime="text/plain",
                                            key=f"download_current_{i}"
                                        )
                                    with col2:
                                        st.button(
                                            f"Copy to Clipboard",
                                            key=f"copy_{i}",
                                            on_click=lambda: st.write("Code copied to clipboard!")
                                        )
                                    
                                    # Generate test button for Java files that are not already test files
                                    if file_type == "java" and category == "main" and "@Test" not in code:
                                        with col3:
                                            if st.button(f"Generate Test", key=f"test_{i}"):
                                                test_code, test_class_name = generate_tests(code, filename)
                                                if test_code:
                                                    test_filename = f"{test_class_name}.java"
                                                    st.session_state.test_files[test_filename] = test_code
                                                    
                                                    # Add to test category
                                                    if test_filename not in st.session_state.file_categories["test"]:
                                                        st.session_state.file_categories["test"].append(test_filename)
                                                    
                                                    # Display the generated test
                                                    st.success(f"Test generated: {test_filename}")
                                                    test_highlighted, _ = get_highlighted_code(test_code, "java")
                                                    st.markdown(test_highlighted, unsafe_allow_html=True)
                                                    
                                                    st.download_button(
                                                        label=f"Download {test_filename}",
                                                        data=test_code,
                                                        file_name=test_filename,
                                                        mime="text/plain",
                                                        key=f"download_test_{i}"
                                                    )
                                                else:
                                                    st.error(f"Failed to generate test: {test_class_name}")
                    else:
                        add_log("WARNING", "No code blocks found in the response")
                        # Only show this warning if we got a response but no code blocks
                        if "create" in prompt.lower() or "generate" in prompt.lower() or "code" in prompt.lower():
                            st.warning("No code blocks were detected in the response. The model provided a text explanation only.")
                
                    # Add assistant response to chat history
                    st.session_state.messages.append({"role": "assistant", "content": full_response})
            
            except Exception as e:
                error_msg = f"Error: {str(e)}"
                add_log("ERROR", error_msg)
                message_placeholder.error(error_msg)
                st.error("""
                An error occurred. Try these troubleshooting steps:
                
                1. Check if Ollama is running with `ollama serve`
                2. Make sure you've pulled the model with `ollama pull mistral`
                3. Try the "Test Ollama Connection" button in the sidebar
                4. Check the Debug Logs in the sidebar for more details
                5. Restart both Ollama and this Streamlit app
                """)

with tab2:  # Project Files Tab
    st.header("📁 Project Files")
    
    # Project file management
    col1, col2 = st.columns([2, 1])
    
    with col1:
        if st.session_state.generated_files or st.session_state.test_files:
            all_files = {**st.session_state.generated_files, **st.session_state.test_files}
            
            # Download options
            download_options = st.radio(
                "Download Options",
                ["Standard ZIP", "Spring Initializr Project"],
                horizontal=True
            )
            
            if st.button("Download Project as ZIP"):
                include_spring_initializr = download_options == "Spring Initializr Project"
                zip_data = generate_zip_file(all_files, include_spring_initializr=include_spring_initializr)
                
                project_name = st.session_state.project_metadata["app_name"].lower().replace(" ", "-")
                st.download_button(
                    label="Download Project ZIP",
                    data=zip_data,
                    file_name=f"{project_name}.zip",
                    mime="application/zip",
                    key="download_project_zip"
                )
            
            # File browser with categories
            file_tabs = st.tabs(["All Files", "Source Code", "Tests", "Configuration"])
            
            with file_tabs[0]:  # All Files
                if all_files:
                    for filename, content in all_files.items():
                        file_type = detect_file_type(content)
                        with st.expander(f"{filename} ({file_type})"):
                            highlighted_code, css = get_highlighted_code(content, file_type)
                            st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                            st.markdown(highlighted_code, unsafe_allow_html=True)
                            
                            col1, col2 = st.columns(2)
                            with col1:
                                st.download_button(
                                    label=f"Download {filename}",
                                    data=content,
                                    file_name=filename,
                                    mime="text/plain",
                                    key=f"download_all_{filename}"
                                )
                            with col2:
                                if file_type == "java" and "@Test" not in content:
                                    if st.button(f"Generate Test for {filename}", key=f"gen_test_{filename}"):
                                        test_code, test_class_name = generate_tests(content, filename)
                                        if test_code:
                                            test_filename = f"{test_class_name}.java"
                                            st.session_state.test_files[test_filename] = test_code
                                            
                                            # Add to test category
                                            if test_filename not in st.session_state.file_categories["test"]:
                                                st.session_state.file_categories["test"].append(test_filename)
                                            
                                            st.success(f"Test generated: {test_filename}")
                                        else:
                                            st.error(f"Failed to generate test")
                else:
                    st.info("No files have been generated yet. Start a conversation to generate code.")
            
            with file_tabs[1]:  # Source Code
                if st.session_state.file_categories["main"]:
                    for filename in st.session_state.file_categories["main"]:
                        if filename in all_files:
                            content = all_files[filename]
                            file_type = detect_file_type(content)
                            with st.expander(f"{filename} ({file_type})"):
                                highlighted_code, css = get_highlighted_code(content, file_type)
                                st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                                st.markdown(highlighted_code, unsafe_allow_html=True)
                                
                                col1, col2 = st.columns(2)
                                with col1:
                                    st.download_button(
                                        label=f"Download {filename}",
                                        data=content,
                                        file_name=filename,
                                        mime="text/plain",
                                        key=f"download_src_{filename}"
                                    )
                                with col2:
                                    if file_type == "java" and "@Test" not in content:
                                        if st.button(f"Generate Test for {filename}", key=f"gen_src_test_{filename}"):
                                            test_code, test_class_name = generate_tests(content, filename)
                                            if test_code:
                                                test_filename = f"{test_class_name}.java"
                                                st.session_state.test_files[test_filename] = test_code
                                                
                                                # Add to test category
                                                if test_filename not in st.session_state.file_categories["test"]:
                                                    st.session_state.file_categories["test"].append(test_filename)
                                                
                                                st.success(f"Test generated: {test_filename}")
                                            else:
                                                st.error(f"Failed to generate test")
                else:
                    st.info("No source files have been generated yet.")
            
            with file_tabs[2]:  # Tests
                if st.session_state.file_categories["test"]:
                    for filename in st.session_state.file_categories["test"]:
                        if filename in all_files:
                            content = all_files[filename]
                            with st.expander(f"{filename} (java)"):
                                highlighted_code, css = get_highlighted_code(content, "java")
                                st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                                st.markdown(highlighted_code, unsafe_allow_html=True)
                                
                                st.download_button(
                                    label=f"Download {filename}",
                                    data=content,
                                    file_name=filename,
                                    mime="text/plain",
                                    key=f"download_test_{filename}"
                                )
                else:
                    st.info("No test files have been generated yet.")
                    
                # Option to generate integration tests
                if st.session_state.file_categories["main"]:
                    if st.button("Generate Integration Tests"):
                        integration_test_code, test_class_name = generate_integration_tests()
                        if integration_test_code:
                            test_filename = f"{test_class_name}.java"
                            st.session_state.test_files[test_filename] = integration_test_code
                            
                            # Add to test category
                            if test_filename not in st.session_state.file_categories["test"]:
                                st.session_state.file_categories["test"].append(test_filename)
                            
                            st.success(f"Integration tests generated: {test_filename}")
                            highlighted_code, css = get_highlighted_code(integration_test_code, "java")
                            st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                            st.markdown(highlighted_code, unsafe_allow_html=True)
                            
                            st.download_button(
                                label=f"Download {test_filename}",
                                data=integration_test_code,
                                file_name=test_filename,
                                mime="text/plain",
                                key=f"download_integration_test"
                            )
                        else:
                            st.error(f"Failed to generate integration tests")
            
            with file_tabs[3]:  # Configuration
                if st.session_state.file_categories["config"]:
                    for filename in st.session_state.file_categories["config"]:
                        if filename in all_files:
                            content = all_files[filename]
                            file_type = detect_file_type(content)
                            with st.expander(f"{filename} ({file_type})"):
                                highlighted_code, css = get_highlighted_code(content, file_type)
                                st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                                st.markdown(highlighted_code, unsafe_allow_html=True)
                                
                                st.download_button(
                                    label=f"Download {filename}",
                                    data=content,
                                    file_name=filename,
                                    mime="text/plain",
                                    key=f"download_config_{filename}"
                                )
                else:
                    st.info("No configuration files have been generated yet.")
        else:
            st.info("No files have been generated yet. Start a conversation to generate code.")
    
    with col2:
        st.subheader("Project Structure")
        
        if st.session_state.generated_files or st.session_state.test_files:
            all_files = {**st.session_state.generated_files, **st.session_state.test_files}
            organized_files = organize_project_files(all_files)
            
            # Display project structure as a tree
            project_structure = ""
            for directory, files in organized_files.items():
                if files:  # Only show directories with files
                    if directory:
                        project_structure += f"📁 {directory}/\n"
                        for filename in files.keys():
                            project_structure += f"  ┗ 📄 {filename}\n"
                    else:
                        project_structure += f"📁 (root)/\n"
                        for filename in files.keys():
                            project_structure += f"  ┗ 📄 {filename}\n"
            
            if project_structure:
                st.code(project_structure, language=None)
            else:
                st.info("No project structure available yet.")
            
            # File statistics
            st.subheader("Project Statistics")
            
            # Count files by type
            file_types = {}
            for filename, content in all_files.items():
                file_type = detect_file_type(content)
                if file_type in file_types:
                    file_types[file_type] += 1
                else:
                    file_types[file_type] = 1
            
            # Display file type counts
            for file_type, count in file_types.items():
                st.text(f"{file_type.upper()}: {count} files")
            
            # Count total lines of code
            total_lines = sum(content.count('\n') + 1 for content in all_files.values())
            st.text(f"Total lines: {total_lines}")
        else:
            st.info("No project structure available yet.")

with tab3:  # Testing Tab
    st.header("🧪 Testing & Quality")
    
    test_col1, test_col2 = st.columns([2, 1])
    
    with test_col1:
        st.subheader("Test Generation")
        
        # Select file to generate tests for
        if st.session_state.file_categories["main"]:
            test_file_options = [""] + [f for f in st.session_state.file_categories["main"] if f.endswith(".java")]
            selected_test_file = st.selectbox("Select a Java file to generate tests for", test_file_options)
            
            if selected_test_file:
                content = st.session_state.generated_files[selected_test_file]
                
                test_type = st.radio(
                    "Test Type",
                    ["Unit Tests", "Integration Tests", "Mock Tests"],
                    horizontal=True
                )
                
                if st.button("Generate Test for Selected File"):
                    test_code, test_class_name = generate_tests(content, selected_test_file)
                    if test_code:
                        test_filename = f"{test_class_name}.java"
                        st.session_state.test_files[test_filename] = test_code
                        
                        # Add to test category
                        if test_filename not in st.session_state.file_categories["test"]:
                            st.session_state.file_categories["test"].append(test_filename)
                        
                        st.success(f"Test generated: {test_filename}")
                        highlighted_code, css = get_highlighted_code(test_code, "java")
                        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                        st.markdown(highlighted_code, unsafe_allow_html=True)
                        
                        st.download_button(
                            label=f"Download {test_filename}",
                            data=test_code,
                            file_name=test_filename,
                            mime="text/plain",
                            key=f"download_tab3_test"
                        )
                    else:
                        st.error(f"Failed to generate test")
            
            # Generate tests for all files
            if st.button("Generate Tests for All Java Files"):
                with st.spinner("Generating tests for all Java files..."):
                    for filename in st.session_state.file_categories["main"]:
                        if filename.endswith(".java") and "@Test" not in st.session_state.generated_files[filename]:
                            content = st.session_state.generated_files[filename]
                            test_code, test_class_name = generate_tests(content, filename)
                            if test_code:
                                test_filename = f"{test_class_name}.java"
                                st.session_state.test_files[test_filename] = test_code
                                
                                # Add to test category
                                if test_filename not in st.session_state.file_categories["test"]:
                                    st.session_state.file_categories["test"].append(test_filename)
                    
                    st.success(f"Generated tests for all Java files. {len(st.session_state.file_categories['test'])} test files created.")
        else:
            st.info("No Java files available to generate tests for. Generate some code first.")
        
        # Integration tests section
        st.subheader("Integration Tests")
        
        if st.session_state.file_categories["main"]:
            if st.button("Generate API Integration Tests"):
                integration_test_code, test_class_name = generate_integration_tests()
                if integration_test_code:
                    test_filename = f"{test_class_name}.java"
                    st.session_state.test_files[test_filename] = integration_test_code
                    
                    # Add to test category
                    if test_filename not in st.session_state.file_categories["test"]:
                        st.session_state.file_categories["test"].append(test_filename)
                    
                    st.success(f"Integration tests generated: {test_filename}")
                    highlighted_code, css = get_highlighted_code(integration_test_code, "java")
                    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                    st.markdown(highlighted_code, unsafe_allow_html=True)
                    
                    st.download_button(
                        label=f"Download {test_filename}",
                        data=integration_test_code,
                        file_name=test_filename,
                        mime="text/plain",
                        key=f"download_tab3_integration_test"
                    )
                else:
                    st.error(f"Failed to generate integration tests")
        else:
            st.info("No Java files available to generate integration tests for.")
    
    with test_col2:
        st.subheader("Generated Tests")
        
        if st.session_state.file_categories["test"]:
            for filename in st.session_state.file_categories["test"]:
                with st.expander(filename):
                    content = st.session_state.test_files[filename]
                    highlighted_code, css = get_highlighted_code(content, "java")
                    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                    st.markdown(highlighted_code, unsafe_allow_html=True)
                    
                    st.download_button(
                        label=f"Download {filename}",
                        data=content,
                        file_name=filename,
                        mime="text/plain",
                        key=f"download_test_tab3_{filename}"
                    )
        else:
            st.info("No test files have been generated yet.")

with tab4:  # Deployment Tab
    st.header("🚀 Deployment & Operations")
    
    deploy_col1, deploy_col2 = st.columns([2, 1])
    
    with deploy_col1:
        st.subheader("Docker Configuration")
        
        # Docker file generation
        if st.button("Generate Docker Configuration"):
            dockerfile, docker_compose = generate_docker_files()
            if dockerfile:
                st.session_state.generated_files["Dockerfile"] = dockerfile
                if "Dockerfile" not in st.session_state.file_categories["config"]:
                    st.session_state.file_categories["config"].append("Dockerfile")
                
                if docker_compose:
                    st.session_state.generated_files["docker-compose.yml"] = docker_compose
                    if "docker-compose.yml" not in st.session_state.file_categories["config"]:
                        st.session_state.file_categories["config"].append("docker-compose.yml")
                
                st.success("Docker configuration generated successfully!")
                
                # Display Dockerfile
                st.subheader("Dockerfile")
                highlighted_dockerfile, css = get_highlighted_code(dockerfile, "text")
                st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                st.markdown(highlighted_dockerfile, unsafe_allow_html=True)
                
                st.download_button(
                    label="Download Dockerfile",
                    data=dockerfile,
                    file_name="Dockerfile",
                    mime="text/plain",
                    key="download_dockerfile"
                )
                
                # Display docker-compose.yml if generated
                if docker_compose:
                    st.subheader("docker-compose.yml")
                    highlighted_compose, _ = get_highlighted_code(docker_compose, "yaml")
                    st.markdown(highlighted_compose, unsafe_allow_html=True)
                    
                    st.download_button(
                        label="Download docker-compose.yml",
                        data=docker_compose,
                        file_name="docker-compose.yml",
                        mime="text/plain",
                        key="download_docker_compose"
                    )
            else:
                st.error("Failed to generate Docker configuration")
        
        # GitHub Actions workflow
        st.subheader("CI/CD Configuration")
        
        if st.button("Generate GitHub Actions Workflow"):
            github_workflow = generate_github_actions()
            if github_workflow:
                st.session_state.generated_files[".github/workflows/ci-cd.yml"] = github_workflow
                if ".github/workflows/ci-cd.yml" not in st.session_state.file_categories["config"]:
                    st.session_state.file_categories["config"].append(".github/workflows/ci-cd.yml")
                
                st.success("GitHub Actions workflow generated successfully!")
                
                # Display workflow file
                highlighted_workflow, css = get_highlighted_code(github_workflow, "yaml")
                st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                st.markdown(highlighted_workflow, unsafe_allow_html=True)
                
                st.download_button(
                    label="Download GitHub Actions Workflow",
                    data=github_workflow,
                    file_name="ci-cd.yml",
                    mime="text/plain",
                    key="download_github_actions"
                )
            else:
                st.error("Failed to generate GitHub Actions workflow")
        
        # Local execution section
        st.subheader("Local Execution")
        
        if st.button("Build & Run Project (Simulation)"):
            with st.spinner("Building and running project..."):
                result = run_project_locally()
                
                if result["success"]:
                    st.success(result["message"])
                    st.code(result["output"], language="bash")
                else:
                    st.error(result["message"])
                    if result["output"]:
                        st.code(result["output"], language="bash")
    
    with deploy_col2:
        st.subheader("Deployment Guides")
        
        deployment_options = [
            "Docker",
            "Kubernetes",
            "AWS",
            "Azure",
            "Google Cloud",
            "Heroku"
        ]
        
        selected_deployment = st.selectbox("Select Deployment Target", deployment_options)
        
        if selected_deployment and st.button(f"Generate {selected_deployment} Deployment Guide"):
            st.info(f"Generating {selected_deployment} deployment guide...")
            # This would typically call another LLM function to generate the guide
            # For now, just display a placeholder
            st.success(f"{selected_deployment} deployment guide would be generated here.")
        
        # Infrastructure as Code
        st.subheader("Infrastructure as Code")
        
        iac_options = [
            "Terraform",
            "AWS CloudFormation",
            "Azure Resource Manager",
            "Kubernetes Manifests"
        ]
        
        selected_iac = st.selectbox("Select IaC Tool", iac_options)
        
        if selected_iac and st.button(f"Generate {selected_iac} Template"):
            st.info(f"Generating {selected_iac} template...")
            # This would typically call another LLM function to generate the IaC template
            # For now, just display a placeholder
            st.success(f"{selected_iac} template would be generated here.")

with tab5:  # Documentation Tab
    st.header("📚 Documentation")
    
    doc_col1, doc_col2 = st.columns([2, 1])
    
    with doc_col1:
        st.subheader("Project Documentation")
        
        if st.button("Generate Project Documentation"):
            documentation = generate_documentation()
            if documentation:
                st.session_state.generated_files["README.md"] = documentation
                if "README.md" not in st.session_state.file_categories["config"]:
                    st.session_state.file_categories["config"].append("README.md")
                
                st.success("Project documentation generated successfully!")
                st.markdown(documentation)
                
                st.download_button(
                    label="Download README.md",
                    data=documentation,
                    file_name="README.md",
                    mime="text/plain",
                    key="download_readme"
                )
            else:
                st.error("Failed to generate project documentation")
        
        # API Documentation section
        st.subheader("API Documentation")
        
        if st.button("Generate OpenAPI Specification"):
            openapi_spec = generate_openapi_spec()
            if openapi_spec:
                st.session_state.generated_files["openapi.yml"] = openapi_spec
                if "openapi.yml" not in st.session_state.file_categories["config"]:
                    st.session_state.file_categories["config"].append("openapi.yml")
                
                st.success("OpenAPI specification generated successfully!")
                
                # Display OpenAPI spec
                highlighted_spec, css = get_highlighted_code(openapi_spec, "yaml")
                st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                st.markdown(highlighted_spec, unsafe_allow_html=True)
                
                st.download_button(
                    label="Download OpenAPI Specification",
                    data=openapi_spec,
                    file_name="openapi.yml",
                    mime="text/plain",
                    key="download_openapi"
                )
            else:
                st.error("Failed to generate OpenAPI specification")
    
    with doc_col2:
        st.subheader("Documentation Files")
        
        # Display documentation files if available
        doc_files = [f for f in st.session_state.file_categories["config"] if f.endswith(".md") or f.endswith(".yml")]
        
        if doc_files:
            for filename in doc_files:
                with st.expander(filename):
                    content = st.session_state.generated_files[filename]
                    file_type = "markdown" if filename.endswith(".md") else "yaml"
                    highlighted_code, css = get_highlighted_code(content, file_type)
                    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
                    st.markdown(highlighted_code, unsafe_allow_html=True)
                    
                    st.download_button(
                        label=f"Download {filename}",
                        data=content,
                        file_name=filename,
                        mime="text/plain",
                        key=f"download_doc_{filename}"
                    )
        else:
            st.info("No documentation files have been generated yet.")
        
        # Documentation templates section
        st.subheader("Documentation Templates")
        
        doc_templates = [
            "Project README",
            "API Documentation",
            "Developer Guide",
            "Architecture Overview",
            "User Manual"
        ]
        
        selected_template = st.selectbox("Select Template", doc_templates)
        
        if selected_template and st.button(f"Generate {selected_template}"):
            st.info(f"Generating {selected_template}...")
            # This would typically call another LLM function to generate the documentation
            # For now, just display a placeholder
            st.success(f"{selected_template} would be generated here.")

# Footer section
st.markdown("---")
st.markdown("💡 Java Spring Boot Developer Assistant | Powered by Ollama and LLM technology")

# Feature documentation expander at the bottom
with st.expander("Available Features"):
    feature_tabs = st.tabs(["Code Generation", "Testing", "Deployment", "Documentation", "New Features"])
    
    with feature_tabs[0]:
        st.markdown("""
        ### 🧩 Code Generation
        
        This assistant can help you generate:
        
        - Spring Boot Controllers, Services, and Repositories
        - Entity classes with JPA annotations
        - Spring Security configurations
        - Spring Data JPA implementations
        - Complete REST APIs
        - Custom configurations
        - Application properties/YAML files
        - Custom exceptions and handlers
        - WebSocket implementations
        - Reactive Spring WebFlux applications
        """)
    
    with feature_tabs[1]:
        st.markdown("""
        ### 🧪 Test Generation
        
        The test generation feature can create:
        
        - JUnit 5 tests with meaningful assertions
        - MockMvc tests for controllers
        - Mockito tests for services
        - Repository tests with @DataJpaTest
        - Integration tests for full API flows
        - WebTestClient tests for WebFlux applications
        - Security tests
        - Performance tests
        
        Click the "Generate Test" button next to any Java class to create a corresponding test class.
        """)
    
    with feature_tabs[2]:
        st.markdown("""
        ### 🚀 Deployment
        
        Deployment features include:
        
        - Docker configuration with multi-stage builds
        - docker-compose setup for local development
        - GitHub Actions CI/CD workflows
        - Kubernetes manifest generation
        - Cloud deployment guides (AWS, Azure, GCP)
        - Infrastructure as Code templates
        - Production-ready configurations
        - Environment-specific setups
        """)
    
    with feature_tabs[3]:
        st.markdown("""
        ### 📚 Documentation
        
        Documentation features include:
        
        - Project README generation
        - OpenAPI specification for REST APIs
        - Developer guides
        - Architecture documentation
        - API usage examples with curl commands
        - Deployment instructions
        - Configuration references
        - Troubleshooting guides
        """)
    
    with feature_tabs[4]:
        st.markdown("""
        ### ✨ New Features
        
        Latest enhancements in this version:
        
        - Support for Spring Boot 3.x features
        - Integration with Spring Initializr for complete project setup
        - Docker and CI/CD configuration generation
        - OpenAPI documentation generation
        - Project statistics and structure visualization
        - Integration tests generation
        - Support for multiple LLM models through Ollama
        - Enhanced code highlighting
        - Project metadata customization
        - Project simulation (build and run)
        """)

# Installation guide expander
with st.expander("Installation & Setup"):
    st.markdown("""
    ### Step 1: Install Prerequisites
    ```bash
    # Install Ollama
    curl -fsSL https://ollama.ai/install.sh | sh
    
    # Pull the Mistral model (recommended)
    ollama pull mistral
    
    # Alternative models
    ollama pull deepseek-coder
    ollama pull codellama
    ollama pull llama3.1
    
    # Install Python dependencies
    pip install streamlit ollama pygments requests
    ```
    
    ### Step 2: Start Ollama Service
    ```bash
    ollama serve
    ```
    
    ### Step 3: Run the Streamlit App
    ```bash
    # Save this code to app.py and run:
    streamlit run app.py
    ```
    
    ### Requirements:
    - Python 3.8+
    - Ollama
    - At least 8GB RAM for running models
    - Java/Maven (optional, for running generated code)
    """)

# Troubleshooting expander
with st.expander("Troubleshooting"):
    st.markdown("""
    ### Common Issues and Solutions
    
    #### Connection Issues
    - **Problem**: Cannot connect to Ollama
      - **Solution**: Ensure Ollama is running with `ollama serve`
      - **Solution**: Check if the Ollama API is accessible at http://localhost:11434
    
    #### Model Issues
    - **Problem**: Model not found or not loading
      - **Solution**: Pull the model first with `ollama pull mistral`
      - **Solution**: Check available models with `ollama list`
      - **Solution**: For larger models, ensure you have sufficient RAM
    
    #### Response Issues
    - **Problem**: Empty or incomplete responses
      - **Solution**: Try a lower temperature setting (0.1-0.3)
      - **Solution**: Break complex requests into smaller ones
      - **Solution**: Try a different model (codellama or deepseek-coder for code)
    
    #### Performance Issues
    - **Problem**: Slow responses
      - **Solution**: Use a smaller model like mistral instead of larger ones
      - **Solution**: Reduce the context length of your conversations
      - **Solution**: Ensure your machine has enough CPU/GPU resources
      
    #### File Generation Issues
    - **Problem**: Incorrect or incomplete code generation
      - **Solution**: Be more specific in your prompt
      - **Solution**: Provide example code or structure in your request
      - **Solution**: Iterate and refine the generated code with follow-up requests
    """)