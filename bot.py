import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import os
import sqlite3
from datetime import datetime, timezone

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = "data.db"
JUDGE_ROLE = "Judge"
GUILD_ID = 1467071917381914749
GUILD = discord.Object(id=GUILD_ID)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_id INTEGER NOT NULL REFERENCES challenges(id),
            season_id INTEGER NOT NULL REFERENCES seasons(id),
            user_id INTEGER NOT NULL,
            file_url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            points INTEGER NOT NULL DEFAULT 0,
            judged_by INTEGER,
            judged_at TEXT,
            created_at TEXT NOT NULL
        );
        """)
        if conn.execute("SELECT id FROM seasons WHERE is_active = 1").fetchone() is None:
            conn.execute(
                "INSERT INTO seasons (name, is_active, created_at) VALUES (?, 1, ?)",
                ("Season 1", now_iso()),
            )


def active_season_id():
    with db() as conn:
        row = conn.execute("SELECT id FROM seasons WHERE is_active = 1").fetchone()
        return row["id"] if row else None


challenge_group = app_commands.Group(name="challenge", description="Challenge commands")


@challenge_group.command(name="post", description="Post a new challenge (Judge only)")
@app_commands.describe(title="Challenge title", description="Challenge description")
@app_commands.checks.has_role(JUDGE_ROLE)
async def challenge_post(interaction: discord.Interaction, title: str, description: str):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO challenges (title, description, created_by, created_at) VALUES (?, ?, ?, ?)",
            (title, description, interaction.user.id, now_iso()),
        )
        challenge_id = cur.lastrowid
    embed = discord.Embed(
        title=f"🏆 New Challenge: {title}",
        description=description,
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"Challenge #{challenge_id} • Posted by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@challenge_group.command(name="list", description="Show all active challenges")
async def challenge_list(interaction: discord.Interaction):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, title, description FROM challenges WHERE status = 'active' ORDER BY id"
        ).fetchall()
    if not rows:
        await interaction.response.send_message("No active challenges.", ephemeral=True)
        return
    embed = discord.Embed(title="📋 Active Challenges", color=discord.Color.blue())
    for r in rows:
        desc = r["description"]
        if len(desc) > 200:
            desc = desc[:197] + "..."
        embed.add_field(name=f"#{r['id']} — {r['title']}", value=desc, inline=False)
    await interaction.response.send_message(embed=embed)


async def active_challenge_autocomplete(interaction: discord.Interaction, current: str):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, title FROM challenges WHERE status = 'active' AND title LIKE ? ORDER BY id LIMIT 25",
            (f"%{current}%",),
        ).fetchall()
    return [app_commands.Choice(name=f"#{r['id']} — {r['title']}"[:100], value=r["id"]) for r in rows]


@challenge_group.command(name="submit", description="Submit proof for a challenge")
@app_commands.describe(challenge="The challenge you're entering", proof="Screenshot or video proof")
@app_commands.autocomplete(challenge=active_challenge_autocomplete)
async def challenge_submit(
    interaction: discord.Interaction,
    challenge: int,
    proof: discord.Attachment,
):
    season_id = active_season_id()
    with db() as conn:
        c = conn.execute(
            "SELECT title FROM challenges WHERE id = ? AND status = 'active'",
            (challenge,),
        ).fetchone()
        if c is None:
            await interaction.response.send_message("That challenge isn't active.", ephemeral=True)
            return
        cur = conn.execute(
            "INSERT INTO submissions (challenge_id, season_id, user_id, file_url, created_at) VALUES (?, ?, ?, ?, ?)",
            (challenge, season_id, interaction.user.id, proof.url, now_iso()),
        )
        submission_id = cur.lastrowid
    embed = discord.Embed(
        title=f"📥 Submission #{submission_id}",
        description=f"By {interaction.user.mention} for **#{challenge} — {c['title']}**",
        color=discord.Color.orange(),
    )
    if proof.content_type and proof.content_type.startswith("image/"):
        embed.set_image(url=proof.url)
    embed.add_field(name="Proof", value=f"[{proof.filename}]({proof.url})")
    embed.set_footer(text="Status: pending judging")
    await interaction.response.send_message(embed=embed)


async def pending_submission_autocomplete(interaction: discord.Interaction, current: str):
    with db() as conn:
        rows = conn.execute(
            """SELECT s.id, s.user_id, c.title
               FROM submissions s
               JOIN challenges c ON c.id = s.challenge_id
               WHERE s.status = 'pending'
               ORDER BY s.id LIMIT 25"""
        ).fetchall()
    return [
        app_commands.Choice(
            name=f"#{r['id']} — {r['title']} (user {r['user_id']})"[:100],
            value=r["id"],
        )
        for r in rows
    ]


@challenge_group.command(name="judge", description="Approve or reject a submission (Judge only)")
@app_commands.describe(
    submission="Pending submission to judge",
    verdict="Approve or reject",
    points="Points to award if approved",
)
@app_commands.choices(verdict=[
    app_commands.Choice(name="Approve", value="approved"),
    app_commands.Choice(name="Reject", value="rejected"),
])
@app_commands.autocomplete(submission=pending_submission_autocomplete)
@app_commands.checks.has_role(JUDGE_ROLE)
async def challenge_judge(
    interaction: discord.Interaction,
    submission: int,
    verdict: app_commands.Choice[str],
    points: int = 0,
):
    if verdict.value == "rejected":
        points = 0
    with db() as conn:
        s = conn.execute(
            "SELECT user_id, status FROM submissions WHERE id = ?", (submission,)
        ).fetchone()
        if s is None:
            await interaction.response.send_message("Submission not found.", ephemeral=True)
            return
        if s["status"] != "pending":
            await interaction.response.send_message(
                f"Submission #{submission} is already {s['status']}.", ephemeral=True
            )
            return
        conn.execute(
            "UPDATE submissions SET status = ?, points = ?, judged_by = ?, judged_at = ? WHERE id = ?",
            (verdict.value, points, interaction.user.id, now_iso(), submission),
        )
    user = interaction.guild.get_member(s["user_id"]) if interaction.guild else None
    user_str = user.mention if user else f"<@{s['user_id']}>"
    color = discord.Color.green() if verdict.value == "approved" else discord.Color.red()
    embed = discord.Embed(
        title=f"⚖️ Submission #{submission} {verdict.value}",
        description=f"By {user_str}\nPoints: **{points}**\nJudged by {interaction.user.mention}",
        color=color,
    )
    await interaction.response.send_message(embed=embed)


bot.tree.add_command(challenge_group)


@bot.tree.command(name="leaderboard", description="Show current season standings")
async def leaderboard(interaction: discord.Interaction):
    season_id = active_season_id()
    with db() as conn:
        season = conn.execute("SELECT name FROM seasons WHERE id = ?", (season_id,)).fetchone()
        rows = conn.execute(
            """SELECT user_id, SUM(points) AS total
               FROM submissions
               WHERE season_id = ? AND status = 'approved'
               GROUP BY user_id
               HAVING total > 0
               ORDER BY total DESC LIMIT 25""",
            (season_id,),
        ).fetchall()
    if not rows:
        await interaction.response.send_message("No points awarded yet this season.", ephemeral=True)
        return
    lines = []
    for i, r in enumerate(rows, start=1):
        member = interaction.guild.get_member(r["user_id"]) if interaction.guild else None
        name = member.display_name if member else f"<@{r['user_id']}>"
        lines.append(f"**{i}.** {name} — {r['total']} pts")
    title = f"🏅 {season['name']} Leaderboard" if season else "🏅 Leaderboard"
    embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.gold())
    await interaction.response.send_message(embed=embed)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingRole):
        msg = f"You need the **{JUDGE_ROLE}** role to use this command."
    else:
        msg = f"Error: {error}"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


@bot.event
async def on_ready():
    init_db()
    bot.tree.copy_global_to(guild=GUILD)
    synced = await bot.tree.sync(guild=GUILD)
    print(f"✅ {bot.user} is online — synced {len(synced)} commands to guild {GUILD_ID}", flush=True)


bot.run(TOKEN)
