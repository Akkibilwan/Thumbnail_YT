import streamlit as st
import sqlite3
import json
import datetime
import requests
import openai
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import isodate

# -----------------------
# SQLite Caching Functions
# -----------------------
def init_db():
    conn = sqlite3.connect("cache.db")
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            data TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def get_cache(key):
    conn = sqlite3.connect("cache.db")
    c = conn.cursor()
    c.execute("SELECT data FROM cache WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None

def set_cache(key, data):
    conn = sqlite3.connect("cache.db")
    c = conn.cursor()
    c.execute("REPLACE INTO cache (key, data) VALUES (?, ?)", (key, json.dumps(data)))
    conn.commit()
    conn.close()

# -----------------------
# Helper Functions
# -----------------------
def compute_outlier(video_views, channel_total_views, channel_video_count):
    try:
        channel_avg = int(channel_total_views) / int(channel_video_count)
        return round(video_views / channel_avg, 2) if channel_avg > 0 else 0
    except Exception:
        return 0

def parse_duration(duration):
    """Parses ISO 8601 duration (e.g. 'PT1H2M10S') into seconds."""
    try:
        td = isodate.parse_duration(duration)
        return int(td.total_seconds())
    except Exception:
        return 0

def classify_videos(videos):
    """Separate videos into regular (longer than 60 seconds) and shorts (60 seconds or less)."""
    regular_videos = []
    shorts = []
    for video in videos:
        duration = video.get("contentDetails", {}).get("duration", "PT0S")
        seconds = parse_duration(duration)
        if seconds <= 60:
            shorts.append(video)
        else:
            regular_videos.append(video)
    return regular_videos, shorts

# -----------------------
# YouTube API Functions
# -----------------------
def youtube_search(query, published_after=None, finance_channels=None):
    youtube = build('youtube', 'v3', developerKey=st.secrets["YOUTUBE_API_KEY"])
    # If finance_channels is provided, restrict search to those channels.
    if finance_channels:
        results = []
        for channel_id in finance_channels:
            try:
                search_response = youtube.search().list(
                    channelId=channel_id,
                    q=query,
                    part="id,snippet",
                    maxResults=10,
                    type="video",
                    publishedAfter=published_after
                ).execute()
                results.extend(search_response.get("items", []))
            except HttpError as e:
                st.error(f"An HTTP error occurred: {e}")
        return results
    else:
        search_response = youtube.search().list(
            q=query,
            part="id,snippet",
            maxResults=20,
            type="video",
            publishedAfter=published_after
        ).execute()
        return search_response.get("items", [])

def get_video_details(video_ids):
    youtube = build('youtube', 'v3', developerKey=st.secrets["YOUTUBE_API_KEY"])
    response = youtube.videos().list(
        id=",".join(video_ids),
        part="statistics,contentDetails,snippet"
    ).execute()
    return response.get("items", [])

def get_channel_details(channel_ids):
    youtube = build('youtube', 'v3', developerKey=st.secrets["YOUTUBE_API_KEY"])
    response = youtube.channels().list(
        id=",".join(channel_ids),
        part="statistics"
    ).execute()
    return response.get("items", [])

# -----------------------
# Vision AI & OpenAI Functions
# -----------------------
def analyze_thumbnail(thumbnail_url):
    # --- Vision AI integration ---
    # Use the Vision AI client ID from st.secrets (replace the URL with the actual endpoint)
    vision_client_id = st.secrets["VISION_AI_CLIENT_ID"]
    vision_api_url = "https://api.visionai.example.com/analyze"  # placeholder endpoint
    headers = {"Authorization": f"Bearer {vision_client_id}"}
    payload = {"image_url": thumbnail_url}
    try:
        vision_response = requests.post(vision_api_url, json=payload, headers=headers)
        vision_data = vision_response.json()
        # Assume vision_data contains a 'description' field
        thumbnail_description = vision_data.get("description", "No description available.")
    except Exception:
        thumbnail_description = "Error analyzing thumbnail."

    # --- OpenAI integration ---
    openai.api_key = st.secrets["OPENAI_API_KEY"]
    prompt = f"Describe what is happening in the thumbnail: {thumbnail_description}"
    try:
        gpt_response = openai.Completion.create(
            engine="text-davinci-003",
            prompt=prompt,
            max_tokens=50,
            n=1,
            temperature=0.7,
        )
        gpt_text = gpt_response.choices[0].text.strip()
    except Exception:
        gpt_text = "Error generating description."

    return thumbnail_description, gpt_text

# -----------------------
# Streamlit App Pages
# -----------------------
def main():
    st.set_page_config(layout="wide")
    init_db()

    # Use session_state to track which page the user is on and store results
    if "page" not in st.session_state:
        st.session_state.page = "search"
    if "results" not in st.session_state:
        st.session_state.results = None
    if "selected_video" not in st.session_state:
        st.session_state.selected_video = None

    if st.session_state.page == "search":
        show_search_page()
    elif st.session_state.page == "analysis":
        show_analysis_page()

def show_search_page():
    st.title("YouTube Outlier Video Search")

    # Choose search type
    search_type = st.radio("Choose Search Type", options=["Generic Search", "Finance Niche Search"])

    # Finance niche additional filter
    finance_channels = None
    if search_type == "Finance Niche Search":
        finance_filter = st.selectbox("Select Finance Filter", options=["India", "USA", "Both"])
        uploaded_file = st.file_uploader("Upload Finance Channels JSON", type=["json"])
        if uploaded_file is not None:
            try:
                finance_data = json.load(uploaded_file)
                # Expected JSON format: {"India": ["channelID1", "channelID2", ...], "USA": ["channelID3", ...]}
                if finance_filter == "India":
                    finance_channels = finance_data.get("India", [])
                elif finance_filter == "USA":
                    finance_channels = finance_data.get("USA", [])
                else:
                    finance_channels = finance_data.get("India", []) + finance_data.get("USA", [])
            except Exception:
                st.error("Invalid JSON format for finance channels.")

    keyword = st.text_input("Enter Keywords or YouTube URL")

    # Upload timeframe filter
    time_filter_option = st.selectbox("Select Upload Timeframe", options=["Any", "24 hours", "48 hours", "7 days", "15 days", "1 month"])
    published_after = None
    if time_filter_option != "Any":
        now = datetime.datetime.utcnow()
        if time_filter_option == "24 hours":
            delta = datetime.timedelta(hours=24)
        elif time_filter_option == "48 hours":
            delta = datetime.timedelta(hours=48)
        elif time_filter_option == "7 days":
            delta = datetime.timedelta(days=7)
        elif time_filter_option == "15 days":
            delta = datetime.timedelta(days=15)
        elif time_filter_option == "1 month":
            delta = datetime.timedelta(days=30)
        published_after = (now - delta).isoformat("T") + "Z"

    # Sorting filter (Views or Outlier Score)
    sort_option = st.selectbox("Sort By", options=["Views", "Outlier Score"])

    if st.button("Search"):
        # Create a cache key from all parameters
        cache_key = f"{search_type}_{finance_channels}_{keyword}_{time_filter_option}_{sort_option}"
        cached_data = get_cache(cache_key)
        if cached_data:
            st.session_state.results = cached_data
        else:
            results = youtube_search(keyword, published_after, finance_channels)
            if not results:
                st.write("No results found.")
                return

            # Get video and channel IDs from search results
            video_ids = [item["id"]["videoId"] for item in results if "videoId" in item["id"]]
            channel_ids = list(set([item["snippet"]["channelId"] for item in results]))
            
            # Batch API calls for video and channel details
            video_details = get_video_details(video_ids)
            channel_details = get_channel_details(channel_ids)
            channel_stats = {channel["id"]: channel["statistics"] for channel in channel_details}
            
            # Merge details and calculate outlier score for each video
            processed_videos = []
            for video in video_details:
                vid_id = video["id"]
                snippet = video.get("snippet", {})
                channel_id = snippet.get("channelId")
                stats = video.get("statistics", {})
                view_count = int(stats.get("viewCount", 0))
                ch_stats = channel_stats.get(channel_id, {})
                total_views = ch_stats.get("viewCount", "0")
                video_count = ch_stats.get("videoCount", "1")
                outlier_score = compute_outlier(view_count, total_views, video_count)
                video["outlier_score"] = outlier_score
                processed_videos.append(video)
            
            # Separate videos into regular and shorts
            regular_videos, shorts = classify_videos(processed_videos)
            
            # Sorting based on selection
            if sort_option == "Views":
                regular_videos.sort(key=lambda x: int(x.get("statistics", {}).get("viewCount", 0)), reverse=True)
                shorts.sort(key=lambda x: int(x.get("statistics", {}).get("viewCount", 0)), reverse=True)
            else:
                regular_videos.sort(key=lambda x: x.get("outlier_score", 0), reverse=True)
                shorts.sort(key=lambda x: x.get("outlier_score", 0), reverse=True)
            
            st.session_state.results = {"regular": regular_videos, "shorts": shorts}
            set_cache(cache_key, st.session_state.results)
        
        # Display search results
        display_results(st.session_state.results)

def display_results(results):
    st.subheader("Regular Videos")
    if results.get("regular"):
        # Display in grid layout (3 cards per row)
        cols = st.columns(3)
        for idx, video in enumerate(results["regular"]):
            col = cols[idx % 3]
            with col:
                thumbnail_url = video.get("snippet", {}).get("thumbnails", {}).get("medium", {}).get("url", "")
                st.image(thumbnail_url, width=450)
                title = video.get("snippet", {}).get("title", "No Title")
                st.write(title)
                view_count = video.get("statistics", {}).get("viewCount", "0")
                st.write(f"Views: {view_count}")
                st.write(f"Outlier Score: {video.get('outlier_score', 0)}")
                if st.button("Analyze Thumbnail", key=f"analyze_{video['id']}"):
                    st.session_state.selected_video = video
                    st.session_state.page = "analysis"
                    st.experimental_rerun()
    else:
        st.write("No regular videos found.")

    st.subheader("Shorts")
    if results.get("shorts"):
        cols = st.columns(3)
        for idx, video in enumerate(results["shorts"]):
            col = cols[idx % 3]
            with col:
                thumbnail_url = video.get("snippet", {}).get("thumbnails", {}).get("medium", {}).get("url", "")
                st.image(thumbnail_url, width=450)
                title = video.get("snippet", {}).get("title", "No Title")
                st.write(title)
                view_count = video.get("statistics", {}).get("viewCount", "0")
                st.write(f"Views: {view_count}")
                st.write(f"Outlier Score: {video.get('outlier_score', 0)}")
                if st.button("Analyze Thumbnail", key=f"analyze_{video['id']}_short"):
                    st.session_state.selected_video = video
                    st.session_state.page = "analysis"
                    st.experimental_rerun()
    else:
        st.write("No shorts found.")

def show_analysis_page():
    st.title("Thumbnail Analysis")
    video = st.session_state.selected_video
    if not video:
        st.error("No video selected.")
        return

    thumbnail_url = video.get("snippet", {}).get("thumbnails", {}).get("medium", {}).get("url", "")
    st.image(thumbnail_url, width=450)
    st.write(video.get("snippet", {}).get("title", "No Title"))

    st.write("Analyzing thumbnail...")
    thumbnail_desc, gpt_result = analyze_thumbnail(thumbnail_url)

    st.write("**Vision AI Analysis:**")
    st.write(thumbnail_desc)

    st.write("**GPT Prompt Output:**")
    st.write(gpt_result)

    if st.button("Back"):
        st.session_state.page = "search"
        st.experimental_rerun()

# -----------------------
# Run the App
# -----------------------
if __name__ == '__main__':
    main()
