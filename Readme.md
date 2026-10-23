# Install Instructions

## Step 1: Install Ollama & Pull Local Models

Install Ollama to host your models locally, then pull Google DeepMind’s Gemma 4 (or any preferred multimodal vision model, like LLaVA or MiniCPM).

1.	Install Ollama:
⚬	macOS / Linux: Download from ollama.com or run:

    ```bash
    brew install ollama
    ```
⚬	Windows: Download and run the setup installer from ollama.com.

2.	Pull the Local Gemma 4 & 2 Models:
Open your terminal and run:

    ```bash
        ollama pull gemma4
    ```

## Step 2: Create a Python Virtual Environment

Keep your dependencies isolated by creating a clean virtual environment:

    ```bash
    # 1. Navigate to your project folder
    cd ai_app

    # 2. Create the virtual environment
    python3 -m venv venv

    # 3. Activate the virtual environment
    # macOS / Linux:
    source venv/bin/activate

    # Windows (PowerShell):
    # .\venv\Scripts\Activate.ps1
    ```

## Step 3: Install dependencies

    ```text
        flask>=3.0.0
        docker>=7.0.0
        httpx>=0.27.0

    ```


    ```bash
    pip install -r requirements.txt
    ```
   

## Step 4: Source documents 

    Copy source documents and Python scripts from the GitHub repositoey

## Step 5: Run tests via CLI

    ```bash
    python3 app.py

    ```
## Step 6: Open SecureAgent

1.	Open your browser and go to http://127.0.0.1:5000
2.	Select source  Documents
3.	Enter the prompt and clock the run button
4.	You can coninue the conversation

-------------------