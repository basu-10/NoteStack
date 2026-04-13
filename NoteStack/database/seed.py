"""
Seed the database with sample prompts, folders, and tags.
Only runs if the database is empty.
"""
from database.db import get_connection, create_folder, create_prompt, delete_prompt, get_stats, get_prompts

SAMPLE_DATA = [
    {
        "title": "React Component Generator",
        "content": (
            "Act as a senior React developer. Create a functional component that handles "
            "a complex form with validation using Zod and React Hook Form. The component "
            "should support dynamic field arrays, conditional rendering based on user role, "
            "and integrate with a REST API for submission. Include TypeScript types."
        ),
        "folder": "Code Generation",
        "tags": ["react", "typescript", "development"],
        "favorite": True,
    },
    {
        "title": "Sci-Fi World Building",
        "content": (
            "Describe a futuristic city built inside a giant hollowed-out asteroid. "
            "Focus on the social hierarchy, the unique architecture adapted to low gravity, "
            "the power sources, trade economy, and the cultural tensions between "
            "long-term residents and newly arrived immigrants."
        ),
        "folder": "Creative Writing",
        "tags": ["creative", "sci-fi", "writing"],
        "favorite": False,
    },
    {
        "title": "Email Marketing Sequence",
        "content": (
            "Write a 3-part email sequence for a new SaaS product launch. "
            "Part 1: Problem awareness. Part 2: Solution introduction. "
            "Part 3: Limited time offer with urgency triggers. "
            "Target audience: B2B marketing managers. Tone: professional but conversational."
        ),
        "folder": "Marketing Copy",
        "tags": ["marketing", "copywriting", "saas"],
        "favorite": True,
    },
    {
        "title": "Python Data Cleaning Script",
        "content": (
            "Generate a Python script using Pandas to clean a CSV file. "
            "Handle missing values by interpolation, remove duplicates, normalize column names, "
            "detect and remove outliers using IQR, and export a cleaned version. "
            "Add logging for each transformation step."
        ),
        "folder": "Data Analysis",
        "tags": ["python", "data", "pandas"],
        "favorite": False,
    },
    {
        "title": "SQL Query Optimizer",
        "content": (
            "Review the following SQL query and suggest optimizations. "
            "Focus on index usage, avoiding N+1 queries, rewriting subqueries as JOINs, "
            "and leveraging window functions where applicable. "
            "Provide before/after comparisons with estimated performance gains."
        ),
        "folder": "Code Generation",
        "tags": ["sql", "database", "dev"],
        "favorite": False,
    },
    {
        "title": "Technical Interview Prep",
        "content": (
            "Act as a senior software engineer conducting a technical interview. "
            "Ask me 5 progressive system design questions for a distributed cache. "
            "After each answer, provide feedback on what was strong, what was missing, "
            "and what a senior candidate would typically cover."
        ),
        "folder": "Creative Writing",
        "tags": ["interview", "learning", "dev"],
        "favorite": False,
    },
    {
        "title": "Blog Post Outline",
        "content": (
            "Create a detailed SEO-optimized blog post outline on the topic of "
            "'Building Scalable Microservices with Go and Kubernetes'. "
            "Include target keywords, meta description, H2/H3 structure, "
            "internal linking suggestions, and a call-to-action."
        ),
        "folder": "Marketing Copy",
        "tags": ["blog", "seo", "frontend"],
        "favorite": False,
    },
    {
        "title": "Mock User Data Generator",
        "content": (
            "Write a Python script to generate 500 realistic mock user records. "
            "Each record should include name, email, avatar URL, subscription tier, "
            "signup date, country, and usage stats. Use the Faker library and export "
            "to both JSON and CSV formats."
        ),
        "folder": "Data Analysis",
        "tags": ["python", "mock-data", "productivity"],
        "favorite": True,
    },
]


def _do_seed():
    folder_ids: dict[str, int] = {}
    for item in SAMPLE_DATA:
        fname = item["folder"]
        if fname not in folder_ids:
            folder_ids[fname] = create_folder(fname)

    for item in SAMPLE_DATA:
        pid = create_prompt(
            title=item["title"],
            content=item["content"],
            folder_id=folder_ids[item["folder"]],
            tag_names=item["tags"],
        )
        if item.get("favorite"):
            conn = get_connection()
            conn.execute("UPDATE prompts SET is_favorite=1 WHERE id=?", (pid,))
            conn.commit()
            conn.close()


def seed_if_empty():
    stats = get_stats()
    if stats["total"] > 0:
        return  # already seeded
    _do_seed()


def seed_force():
    """Insert all sample data regardless of whether the DB is empty."""
    _do_seed()


def remove_sample_data() -> int:
    """Delete prompts whose title matches a sample data title. Returns count removed."""
    sample_titles = {item["title"] for item in SAMPLE_DATA}
    all_prompts = get_prompts()
    to_delete = [p["id"] for p in all_prompts if p["title"] in sample_titles]
    for pid in to_delete:
        delete_prompt(pid)
    return len(to_delete)
