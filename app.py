from flask import Flask, request, render_template, jsonify
import praw
from google.cloud import texttospeech
from google.cloud import speech_v1p1beta1 as speech
from google.cloud import storage
import moviepy.editor as mp
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, AudioFileClip
import os

app = Flask(__name__)

# Reddit API credentials
client_id = os.environ.get('REDDIT_CLIENT_ID')
client_secret = os.environ.get('REDDIT_CLIENT_SECRET')
user_agent = os.environ.get('REDDIT_USER_AGENT')

# Google Text-to-Speech API credentials
google_credentials_json = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS_JSON')
if google_credentials_json:
    with open('google_credentials.json', 'w') as f:
        f.write(google_credentials_json)
KEY_FILE = 'google_credentials.json'

# Google Cloud Storage client
storage_client = storage.Client.from_service_account_json(KEY_FILE)
bucket_name = 'pumpervids'

# Function to download files from Google Cloud Storage
def download_blob(bucket_name, source_blob_name, destination_file_name):
    """Downloads a blob from the bucket."""
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(source_blob_name)
    blob.download_to_filename(destination_file_name)
    print(f"Blob {source_blob_name} downloaded to {destination_file_name}.")

# Paths to media files
BACKGROUND_MUSIC_FILE = 'ScaryMusic.mp3'
VIDEO_FILES = {
    'MC.mp4': 'MC.mp4',
    'CookingVid.mp4': 'CookingVid.mp4'
}

# Download necessary files
download_blob(bucket_name, 'ScaryMusic.mp3', BACKGROUND_MUSIC_FILE)
download_blob(bucket_name, 'MC.mp4', VIDEO_FILES['MC.mp4'])
download_blob(bucket_name, 'CookingVid.mp4', VIDEO_FILES['CookingVid.mp4'])

# Create a Reddit instance
reddit = praw.Reddit(client_id=client_id, client_secret=client_secret, user_agent=user_agent)

def get_reddit_story(subreddit_name, min_words=100, max_words=300):
    subreddit = reddit.subreddit(subreddit_name)
    for submission in subreddit.new(limit=50):
        if not submission.stickied and submission.selftext:
            word_count = len(submission.selftext.split())
            if min_words <= word_count <= max_words:
                return submission.title, submission.selftext, submission.url
    return None, None, None

def synthesize_text(text, output_file):
    client = texttospeech.TextToSpeechClient.from_service_account_json(KEY_FILE)

    input_text = texttospeech.SynthesisInput(text=text)

    voice = texttospeech.VoiceSelectionParams(
        language_code='en-US',
        name='en-US-Wavenet-B',
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=1.35
    )

    response = client.synthesize_speech(
        input=input_text, voice=voice, audio_config=audio_config
    )

    with open(output_file, 'wb') as out:
        out.write(response.audio_content)

def combine_audio_video(audio_file, video_file, background_music_file, output_file):
    video = mp.VideoFileClip(video_file)
    audio = mp.AudioFileClip(audio_file)
    background_music = mp.AudioFileClip(background_music_file).volumex(0.1)  # Set the volume of the background music to 10%

    # Ensure the background music is as long as the audio file
    background_music = background_music.set_duration(audio.duration)

    # Combine the synthesized audio with the background music
    final_audio = mp.CompositeAudioClip([audio, background_music])

    # Ensure video is as long as the audio file, with a tolerance of 1-2 seconds
    end_time = min(video.duration, final_audio.duration + 2)
    trimmed_video = video.subclip(0, end_time).set_audio(final_audio)

    trimmed_video.write_videofile(output_file, codec='libx264', audio_codec='aac')

def transcribe_audio(audio_file):
    client = speech.SpeechClient.from_service_account_json(KEY_FILE)
    with open(audio_file, 'rb') as audio:
        content = audio.read()
    audio = speech.RecognitionAudio(content=content)
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.MP3,
        sample_rate_hertz=16000,
        language_code="en-US",
        enable_word_time_offsets=True,
    )
    response = client.recognize(config=config, audio=audio)

    words_info = []
    for result in response.results:
        alternative = result.alternatives[0]
        for word_info in alternative.words:
            word = word_info.word
            start_time = word_info.start_time.total_seconds()
            end_time = word_info.end_time.total_seconds()
            words_info.append((word, start_time, end_time))

    return words_info

def add_text_captions(video_file, words_info, output_file):
    video = VideoFileClip(video_file)
    font_path = 'fonts/Gilroy-ExtraBold.otf'  # Adjust this path as needed

    text_clips = []
    current_clip_start = None
    current_clip_end = None
    current_text = ''
    word_count = 0
    for word, start_time, end_time in words_info:
        if current_clip_start is None:
            current_clip_start = start_time
            current_text += word
            word_count += 1
        else:
            if word_count < 4 and start_time - current_clip_end <= 1:  # If the gap between words is less than 1 second and less than 4 words have been added, continue the current clip
                current_text += ' ' + word
                word_count += 1
            else:
                txt_clip = (TextClip(current_text, fontsize=48, color='white', stroke_color='black', stroke_width=2, font=font_path, method='label')
                            .set_position('center')
                            .set_start(current_clip_start)
                            .set_duration(current_clip_end - current_clip_start))
                text_clips.append(txt_clip)
                # Reset variables for the new clip
                current_clip_start = start_time
                current_text = word
                word_count = 1
        current_clip_end = end_time

    # Add the last clip
    if current_text:
        txt_clip = (TextClip(current_text, fontsize=48, color='white', stroke_color='black', stroke_width=2, font=font_path, method='label')
                    .set_position('center')
                    .set_start(current_clip_start)
                    .set_duration(current_clip_end - current_clip_start))
        text_clips.append(txt_clip)

    result = CompositeVideoClip([video, *text_clips])
    result.write_videofile(output_file, codec='libx264', audio_codec='aac')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_story_text', methods=['POST'])
def get_story_text():
    data = request.json
    subreddit_name = data.get('subreddit', 'horrorstories')
    min_words = int(data.get('min_words', 150))
    max_words = int(data.get('max_words', 300))

    title, story, url = get_reddit_story(subreddit_name, min_words, max_words)

    if title and story:
        return jsonify({'title': title, 'story': story})
    else:
        return jsonify({'error': 'No suitable story found.'}), 404

@app.route('/make_video', methods=['POST'])
def make_video():
    data = request.json
    catchy_title = data.get('catchy_title')
    story = data.get('story')
    background_video = data.get('background_video')
    full_story_text = f"{catchy_title}. {story}"
    output_audio = 'static/story.mp3'
    output_video = 'static/final_story_video.mp4'
    final_output_video = 'static/final_story_video_with_captions.mp4'

    synthesize_text(full_story_text, output_audio)
    combine_audio_video(output_audio, VIDEO_FILES[background_video], BACKGROUND_MUSIC_FILE, output_video)
    words_info = transcribe_audio(output_audio)
    add_text_captions(output_video, words_info, final_output_video)

    return jsonify({'video_url': final_output_video})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
