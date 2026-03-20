import asyncio
import os
import json
from dotenv import load_dotenv

# Load all environment variables
load_dotenv()

from services.story_orchestrator import get_story_orchestrator

async def main():
    print("🚀 Initializing Real Story Generation Test...\n")
    orchestrator = get_story_orchestrator()
    
    # We'll do a shorter story (2 scenes) to make the test faster
    idea = "A brave little Indian boy who discovers a magical flying carpet in his grandmother's attic"
    genre = "adventure"
    num_scenes = 2
    child_age = 6
    
    print(f"📖 Idea: {idea}")
    print(f"🎭 Genre: {genre} | 🎬 Scenes: {num_scenes} | 👧 Age: {child_age}")
    print("\n⏳ Generating story structure via Groq and images via Replicate (Flux)... this might take up to 30-60 seconds...")
    
    try:
        story = await orchestrator.generate_story(
            idea=idea,
            genre=genre,
            num_scenes=num_scenes,
            child_age=child_age
        )
        
        print("\n✅ STORY GENERATION SUCCESSFUL!\n")
        print("="*60)
        print(f"TITLE: {story['title']}")
        print(f"Total Scenes: {story['total_scenes']} | Images Generated: {story['images_generated']}")
        print("="*60)
        
        for scene in story['scenes']:
            print(f"\n--- Scene {scene['scene_number']} ---")
            print(f"TEXT: {scene['story_text']}\n")
            print(f"PROMPT: {scene['image_prompt']}\n")
            if scene['image_url']:
                print(f"IMAGE URL RAW: {scene['image_url']}")  # no emoji, no formatting
            else:
                print("❌ IMAGE FAILED TO GENERATE")
            
            if scene['audio_url']:
                print(f"🎧 AUDIO URL: {scene['audio_url']}")
            else:
                print("❌ AUDIO FAILED TO GENERATE")
                
    except Exception as e:
        print(f"\n❌ ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(main())
