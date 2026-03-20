import asyncio
import os
from dotenv import load_dotenv

# Load all environment variables
load_dotenv()

from services.tts_generation import get_tts_service

async def main():
    print("🚀 Initializing Simple Audio Generation Test...\n")
    service = get_tts_service()
    
    text = "Once upon a time, a brave little Indian boy discovered a magical flying carpet in a dusty attic."
    
    print(f"🎤 Text: {text}")
    print("\n⏳ Generating audio via Replicate (Kokoro 82M)... this might take 10-30 seconds...")
    
    try:
        url = await service.generate_speech(
            text=text,
            voice="af_heart", 
            speed=1.0
        )
        
        if url:
            print(f"\n✅ SUCCESS! Audio generated at:")
            print(f"🔗 {url}")
        else:
            print("\n❌ FAILED: Generation returned None. Check your Replicate billing or rate limits, or the model ID.")
            
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(main())
