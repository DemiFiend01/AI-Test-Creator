from google import genai
from dotenv import load_dotenv
import os

load_dotenv()

client = genai.Client(api_key=os.getenv("API_KEY"))

interaction = client.interactions.create(
    model="gemini-3.8-flash",
    input="Tell me how this model and API works and how to modify the model to get the most out of it."
)
print(interaction.output_text)