# Spring Boot AI Code Assistant

This is an open-source tool that helps you generate, test, and document Spring Boot code using local AI models. It's designed to run entirely on your machine, ensuring privacy and eliminating costs.

## Features

-   **Local AI Model Integration:** Uses Ollama to connect with local LLMs like Mistral, DeepSeek, and Llama.
-   **Code Generation:** Generates complete Spring Boot code examples, REST APIs, entity relationships, and configuration files.
-   **Project Management:** Manages project metadata (application name, Java version, Spring Boot version, etc.).
-   **File Management:** Organizes, views, downloads, and tests generated code files.
-   **Prompt Management:** Includes quick prompts, custom prompts, and chat history.
-   **Debugging:** Provides tools to troubleshoot local model connections.

## Setup

1.  **Install Ollama:** Follow the instructions on the [Ollama website](https://ollama.com/) to install Ollama.

2.  **Pull a Model:** Use Ollama to pull a model (e.g., Mistral):

    ```
    ollama pull mistral
    ```

3.  **Clone the Repository:**

    ```
    git clone https://github.com/dkarthi1973/codeassistant
    cd codeassistant
    ```

4.  **Install Dependencies:**

    ```
    pip install -r requirements.txt
    ```

5.  **Run the Application:**

    ```
    streamlit run SpringbootAIAssistant.py
    ```

## Configuration

-   **Model Selection:** Choose a model in the sidebar.  If the model is not loaded try running:  `ollama pull {model}`
-   **Project Settings:** Configure project metadata like application name, group ID, and artifact ID.
-   **Prompts:** Use quick prompts or add your own custom prompts.

## Usage

1.  **Chat Tab:** Interact with the AI assistant to generate code.
2.  **Project Files Tab:** View and download generated files.
3.  **Testing Tab:** Generate and run tests.
4.  **Deployment Tab:** Find deployment instructions.
5.  **Documentation Tab:** Access generated documentation.

## Contributing

Feel free to fork the repository and contribute to the project. You can add new features, improve the UI, or fix bugs.

## License

This project is open source and available under the [MIT License](LICENSE).

## Credits

-   This project uses [Ollama](https://ollama.com/) for local AI model management.
-   The UI is built with [Streamlit](https://streamlit.io/).

