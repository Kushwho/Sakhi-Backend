import asyncio
import os
from dotenv import load_dotenv

# Load all environment variables
load_dotenv()

from services.image_generation import get_image_service

async def main():
    print("🚀 Initializing Simple Image Generation Test...\n")
    service = get_image_service()
    
    prompt = "A vibrant watercolor painting of a brave little Indian boy discovering a magical flying carpet in a dusty attic, warm golden light, whimsical style."
    
    print(f"🖼️  Prompt: {prompt}")
    print("\n⏳ Generating image via Replicate (Flux Schnell)... this might take 10-30 seconds...")
    
    try:
        url = await service.generate_image(
            prompt=prompt,
            aspect_ratio="1:1"
        )
        
        if url:
            print(f"\n✅ SUCCESS! Image generated at:")
            print(f"🔗 {url}")
        else:
            print("\n❌ FAILED: Generation returned None. Check your Replicate billing or rate limits.")
            
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(main())
