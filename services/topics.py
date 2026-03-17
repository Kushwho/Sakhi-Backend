"""
Sakhi — Curious Mode Topic Catalog
=====================================
Static list of curated topics for children aged 4-12, organized by category
and age range. Used by the ``/api/curious/topics`` endpoint.
"""

import random
from typing import Any

TOPICS: list[dict[str, Any]] = [
    # ── Science ──────────────────────────────────────────────────────

    {
        "id": "science-photosynthesis",
        "title": "How Plants Make Food",
        "emoji": "🌱",
        "description": "Did you know plants cook their own food using sunlight? Let's find out how!",
        "category": "Science",
        "age_range": [6, 12],
        "tags": ["plants", "photosynthesis", "nature", "biology"],
    },
    {
        "id": "science-water-cycle",
        "title": "The Water Cycle",
        "emoji": "💧",
        "description": "Where does rain come from? Follow a water droplet on its amazing journey!",
        "category": "Science",
        "age_range": [5, 10],
        "tags": ["water", "rain", "clouds", "weather"],
    },
    {
        "id": "science-magnets",
        "title": "The Magic of Magnets",
        "emoji": "🧲",
        "description": "Why do some things stick to magnets and others don't? Let's explore!",
        "category": "Science",
        "age_range": [4, 9],
        "tags": ["magnets", "physics", "force"],
    },
    {
        "id": "science-electricity",
        "title": "How Electricity Works",
        "emoji": "⚡",
        "description": "What makes the lights turn on? Discover the spark behind everything!",
        "category": "Science",
        "age_range": [7, 12],
        "tags": ["electricity", "energy", "circuits", "physics"],
    },
    {
        "id": "science-sound",
        "title": "How Sound Travels",
        "emoji": "🔊",
        "description": "Why can you hear your friend calling from far away? Let's explore sound waves!",
        "category": "Science",
        "age_range": [5, 10],
        "tags": ["sound", "waves", "music", "physics"],
    },
    {
        "id": "science-gravity",
        "title": "Why Things Fall Down",
        "emoji": "🍎",
        "description": "Why does a ball always come back down? Discover the force that keeps us on Earth!",
        "category": "Science",
        "age_range": [5, 10],
        "tags": ["gravity", "physics", "newton", "force"],
    },
    # ── Space ────────────────────────────────────────────────────────
    {
        "id": "space-solar-system",
        "title": "Our Solar System",
        "emoji": "🪐",
        "description": "8 amazing planets orbit our Sun — let's visit each one!",
        "category": "Space",
        "age_range": [5, 12],
        "tags": ["planets", "solar system", "sun", "space"],
    },
    {
        "id": "space-moon",
        "title": "The Moon",
        "emoji": "🌙",
        "description": "Why does the Moon change shape? And did people really walk on it?",
        "category": "Space",
        "age_range": [4, 10],
        "tags": ["moon", "space", "phases", "astronauts"],
    },
    {
        "id": "space-stars",
        "title": "What Are Stars?",
        "emoji": "⭐",
        "description": "Those tiny lights in the sky are actually GIANT balls of fire! Let's learn more.",
        "category": "Space",
        "age_range": [5, 10],
        "tags": ["stars", "space", "constellations", "night sky"],
    },
    {
        "id": "space-black-holes",
        "title": "Black Holes",
        "emoji": "🕳️",
        "description": "What happens when a star dies? Explore the most mysterious objects in space!",
        "category": "Space",
        "age_range": [8, 12],
        "tags": ["black holes", "space", "stars", "gravity"],
    },
    {
        "id": "space-isro",
        "title": "India in Space (ISRO)",
        "emoji": "🚀",
        "description": "Did you know India sent a spacecraft to Mars? Discover ISRO's amazing missions!",
        "category": "Space",
        "age_range": [7, 12],
        "tags": ["ISRO", "India", "space", "rockets", "Chandrayaan"],
    },
    # ── Nature & Animals ─────────────────────────────────────────────
    {
        "id": "nature-dinosaurs",
        "title": "Dinosaurs",
        "emoji": "🦕",
        "description": "Giant creatures ruled the Earth millions of years ago — what happened to them?",
        "category": "Nature",
        "age_range": [4, 12],
        "tags": ["dinosaurs", "fossils", "extinction", "prehistoric"],
    },
    {
        "id": "nature-ocean",
        "title": "Deep Ocean Secrets",
        "emoji": "🌊",
        "description": "The ocean is deeper than Mount Everest is tall! What lives down there?",
        "category": "Nature",
        "age_range": [6, 12],
        "tags": ["ocean", "sea", "fish", "deep sea", "marine"],
    },
    {
        "id": "nature-rainforest",
        "title": "Rainforests",
        "emoji": "🌳",
        "description": "Half of all animal species live in rainforests! Let's explore these amazing jungles.",
        "category": "Nature",
        "age_range": [6, 11],
        "tags": ["rainforest", "jungle", "animals", "trees"],
    },
    {
        "id": "nature-butterflies",
        "title": "Butterflies & Metamorphosis",
        "emoji": "🦋",
        "description": "How does a tiny caterpillar become a beautiful butterfly? It's like magic!",
        "category": "Nature",
        "age_range": [4, 8],
        "tags": ["butterflies", "insects", "metamorphosis", "nature"],
    },
    {
        "id": "nature-tigers",
        "title": "Tigers of India",
        "emoji": "🐅",
        "description": "India is home to most of the world's tigers! Let's learn about these magnificent cats.",
        "category": "Nature",
        "age_range": [5, 11],
        "tags": ["tigers", "India", "wildlife", "conservation"],
    },
    {
        "id": "nature-volcanoes",
        "title": "Volcanoes",
        "emoji": "🌋",
        "description": "Mountains that can explode with hot lava! How do volcanoes work?",
        "category": "Nature",
        "age_range": [6, 12],
        "tags": ["volcanoes", "lava", "earth", "geology"],
    },
    # ── Human Body ───────────────────────────────────────────────────
    {
        "id": "body-heart",
        "title": "Your Amazing Heart",
        "emoji": "❤️",
        "description": "Your heart beats about 100,000 times a day! How does it keep you alive?",
        "category": "Body",
        "age_range": [5, 11],
        "tags": ["heart", "blood", "body", "health"],
    },
    {
        "id": "body-brain",
        "title": "The Brain",
        "emoji": "🧠",
        "description": "Your brain is the most powerful computer in the world! Let's see how it works.",
        "category": "Body",
        "age_range": [6, 12],
        "tags": ["brain", "thinking", "neurons", "body"],
    },
    {
        "id": "body-bones",
        "title": "Your Skeleton",
        "emoji": "🦴",
        "description": "You have 206 bones holding you up! Did you know babies have more?",
        "category": "Body",
        "age_range": [5, 10],
        "tags": ["bones", "skeleton", "body", "muscles"],
    },
    {
        "id": "body-food",
        "title": "What Happens to Food?",
        "emoji": "🍕",
        "description": "After you eat your roti, where does it go? Let's follow its journey!",
        "category": "Body",
        "age_range": [5, 10],
        "tags": ["digestion", "food", "stomach", "body"],
    },
    # ── Math ─────────────────────────────────────────────────────────
    {
        "id": "math-zero",
        "title": "The Invention of Zero",
        "emoji": "0️⃣",
        "description": "Did you know zero was invented in India? It changed math forever!",
        "category": "Math",
        "age_range": [6, 12],
        "tags": ["zero", "numbers", "India", "history", "math"],
    },
    {
        "id": "math-shapes",
        "title": "Shapes All Around Us",
        "emoji": "🔷",
        "description": "From honeycombs to pizza slices — shapes are everywhere! Can you spot them?",
        "category": "Math",
        "age_range": [4, 8],
        "tags": ["shapes", "geometry", "patterns", "math"],
    },
    {
        "id": "math-patterns",
        "title": "Patterns in Nature",
        "emoji": "🐚",
        "description": "Sunflowers, seashells, and pinecones all hide a secret number pattern!",
        "category": "Math",
        "age_range": [7, 12],
        "tags": ["patterns", "fibonacci", "nature", "math"],
    },
    {
        "id": "math-fractions",
        "title": "Fractions Are Fun",
        "emoji": "🍰",
        "description": "Sharing a cake equally? That's fractions! Let's make them easy.",
        "category": "Math",
        "age_range": [6, 10],
        "tags": ["fractions", "numbers", "sharing", "math"],
    },
    # ── History & Culture ────────────────────────────────────────────
    {
        "id": "history-indus",
        "title": "Indus Valley Civilization",
        "emoji": "🏛️",
        "description": "5000 years ago, one of the world's first cities was right here in India!",
        "category": "History",
        "age_range": [7, 12],
        "tags": ["Indus Valley", "Harappa", "Mohenjo-daro", "ancient India"],
    },
    {
        "id": "history-freedom",
        "title": "India's Freedom Struggle",
        "emoji": "🇮🇳",
        "description": "How did India become free? Meet the brave leaders who made it happen!",
        "category": "History",
        "age_range": [7, 12],
        "tags": ["freedom", "independence", "Gandhi", "India", "history"],
    },
    {
        "id": "history-inventions",
        "title": "Amazing Indian Inventions",
        "emoji": "💡",
        "description": "From buttons to shampoo — so many everyday things were invented in India!",
        "category": "History",
        "age_range": [6, 12],
        "tags": ["inventions", "India", "innovation", "history"],
    },
    {
        "id": "culture-festivals",
        "title": "Festivals of India",
        "emoji": "🪔",
        "description": "Diwali, Holi, Eid, Christmas — India celebrates so many festivals! Why?",
        "category": "Culture",
        "age_range": [4, 10],
        "tags": ["festivals", "Diwali", "Holi", "culture", "India"],
    },
    {
        "id": "culture-languages",
        "title": "Languages of India",
        "emoji": "🗣️",
        "description": "India has 22 official languages and hundreds more! How did that happen?",
        "category": "Culture",
        "age_range": [6, 11],
        "tags": ["languages", "India", "diversity", "culture"],
    },
    # ── Technology ───────────────────────────────────────────────────
    {
        "id": "tech-robots",
        "title": "Robots & AI",
        "emoji": "🤖",
        "description": "Can machines think? Let's explore the world of robots and artificial intelligence!",
        "category": "Technology",
        "age_range": [7, 12],
        "tags": ["robots", "AI", "technology", "computers"],
    },
    {
        "id": "tech-internet",
        "title": "How the Internet Works",
        "emoji": "🌐",
        "description": "How does a video get from YouTube to your screen? It's an amazing journey!",
        "category": "Technology",
        "age_range": [8, 12],
        "tags": ["internet", "technology", "data", "computers"],
    },
    {
        "id": "tech-coding",
        "title": "What is Coding?",
        "emoji": "💻",
        "description": "Games, apps, and websites are all made with code! Want to know the secret language?",
        "category": "Technology",
        "age_range": [7, 12],
        "tags": ["coding", "programming", "computers", "technology"],
    },
    # ── Art & Creativity ─────────────────────────────────────────────
    {
        "id": "art-colours",
        "title": "The Science of Colours",
        "emoji": "🎨",
        "description": "Why is the sky blue? Why are sunsets orange? Colours hide cool science!",
        "category": "Art",
        "age_range": [4, 10],
        "tags": ["colours", "light", "rainbow", "art", "science"],
    },
    {
        "id": "art-music",
        "title": "How Music Works",
        "emoji": "🎵",
        "description": "Why do some sounds feel happy and others feel sad? Let's explore music!",
        "category": "Art",
        "age_range": [5, 11],
        "tags": ["music", "sound", "instruments", "art"],
    },
    {
        "id": "art-stories",
        "title": "The Art of Storytelling",
        "emoji": "📖",
        "description": "Panchatantra, Jataka Tales — India is the land of stories! What makes a great story?",
        "category": "Art",
        "age_range": [4, 10],
        "tags": ["stories", "Panchatantra", "storytelling", "India"],
    },
    # ── Environment ──────────────────────────────────────────────────
    {
        "id": "env-climate",
        "title": "Climate Change",
        "emoji": "🌍",
        "description": "Why is the Earth getting warmer? And what can kids do to help?",
        "category": "Environment",
        "age_range": [7, 12],
        "tags": ["climate", "environment", "pollution", "earth"],
    },
    {
        "id": "env-recycling",
        "title": "Reduce, Reuse, Recycle",
        "emoji": "♻️",
        "description": "That plastic bottle can become something new! Let's learn how recycling works.",
        "category": "Environment",
        "age_range": [5, 10],
        "tags": ["recycling", "waste", "environment", "plastic"],
    },
    {
        "id": "env-bees",
        "title": "Why Bees Matter",
        "emoji": "🐝",
        "description": "Without bees, we wouldn't have most fruits and vegetables! Let's find out why.",
        "category": "Environment",
        "age_range": [5, 11],
        "tags": ["bees", "pollination", "nature", "environment"],
    }
]


def get_topics_for_age(age: int) -> list[dict[str, Any]]:
    """Return all topics whose age range includes the given age."""
    return [t for t in TOPICS if t["age_range"][0] <= age <= t["age_range"][1]]



def get_topic_by_id(topic_id: str) -> dict[str, Any] | None:
    """Look up a topic by its ID."""
    for t in TOPICS:
        if t["id"] == topic_id:
            return t
    return None


def get_topics_response(age: int, limit: int = 12) -> list[dict[str, Any]]:
    """Return shuffled, age-filtered topics formatted for the API response."""
    filtered = get_topics_for_age(age)
    random.shuffle(filtered)
    topics = filtered[:limit]
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "emoji": t["emoji"],
            "description": t["description"],
            "category": t["category"],
        }
        for t in topics
    ]
