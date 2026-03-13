"""
Sakhi — Story Seed Script
Run: python seed_stories.py
"""

import asyncio
import asyncpg

DATABASE_URL = "postgresql://neondb_owner:npg_GoZnecPT1Nm8@ep-dawn-term-a1y7ycsj-pooler.ap-southeast-1.aws.neon.tech/sakhi-mvp?sslmode=require&channel_binding=require"  # e.g. postgresql://user:pass@host:5432/dbname

STORIES = [
    {
        "title": "The Clever Little Fox",
        "genre": "fable",
        "age_min": 4,
        "age_max": 8,
        "language": "English",
        "segments": [
            "Once upon a time, in a forest full of tall green trees, there lived a clever little fox named Rusty. Rusty had bright orange fur and the most curious eyes you ever saw.",
            "One day, Rusty found a deep well in the middle of the forest. He leaned over to look inside and saw his own reflection staring back at him. He thought it was another fox!",
            "Rusty called out, 'Hello down there! Would you like to be my friend?' But the reflection just copied everything he said. Rusty laughed and laughed when he finally understood.",
            "From that day on, whenever Rusty felt lonely, he would visit the well — not to talk to another fox, but to remind himself that his own company was pretty wonderful too. And he lived happily ever after.",
        ],
    },
    {
        "title": "Mia and the Magic Garden",
        "genre": "fantasy",
        "age_min": 5,
        "age_max": 10,
        "language": "English",
        "segments": [
            "Mia was seven years old and loved to dig in the mud. One rainy afternoon, she found a tiny golden seed behind her grandmother's house. It shimmered even in the grey light.",
            "She planted the seed in a small pot by her window. Every morning she gave it a little water and whispered, 'Good morning, little seed.' Within three days, a glowing green sprout appeared.",
            "By the end of the week, the sprout had grown into a small tree — with strawberries, apples, and even chocolate flowers all growing on the same branches! Mia couldn't believe her eyes.",
            "Mia shared the fruits with everyone in her neighbourhood. The magic, she realised, wasn't just in the seed. It was in the care and love she had given it every single day.",
        ],
    },
    {
        "title": "Brave Captain Zara",
        "genre": "adventure",
        "age_min": 6,
        "age_max": 12,
        "language": "English",
        "segments": [
            "Captain Zara was the youngest captain on the seven seas. She was only ten years old, but her crew of friendly dolphins and talking parrots trusted her completely.",
            "One stormy night, her ship was blown off course and landed near a mysterious island covered in purple mist. Strange sounds echoed through the fog — but Zara was not afraid.",
            "She rowed a small boat to the shore and discovered the sounds were coming from a baby whale that had gotten tangled in some old fishing nets. It was calling for help!",
            "Zara and her dolphins worked together to free the baby whale. As it swam happily back to sea, it splashed water so high it made a rainbow in the night sky. Zara smiled — another adventure done.",
        ],
    },
]


async def seed():
    print("Connecting to database...")
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as conn:
        for story in STORIES:
            segments = story.pop("segments")
            total_segments = len(segments)

            # Insert story
            story_id = await conn.fetchval(
                """
                INSERT INTO stories (title, genre, age_min, age_max, language, total_segments)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                story["title"],
                story["genre"],
                story["age_min"],
                story["age_max"],
                story["language"],
                total_segments,
            )

            if story_id is None:
                print(f"  Skipped (already exists): {story['title']}")
                continue

            # Insert segments
            for i, content in enumerate(segments, start=1):
                await conn.execute(
                    """
                    INSERT INTO story_segments (story_id, position, content)
                    VALUES ($1, $2, $3)
                    """,
                    story_id,
                    i,
                    content,
                )

            print(f"  Inserted: {story['title']} ({total_segments} segments)")

    await pool.close()
    print("\nDone! Stories seeded successfully.")


if __name__ == "__main__":
    asyncio.run(seed())