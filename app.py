from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
import yt_dlp
import os
import uuid
import imageio_ffmpeg

app = Flask(__name__)
CORS(app)

DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# FFmpeg ka path automatic set karein
FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/info', methods=['POST'])
def get_info():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL daalein'}), 400
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'skip_download': True,
        'nocheckcertificate': True,
        'retries': 5,
        'fragment_retries': 5
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = []
            for f in info.get('formats', []):
                if f.get('url') and f.get('ext') in ['mp4', 'm4a', 'webm']:
                    formats.append({
                        'format_id': f['format_id'],
                        'ext': f['ext'],
                        'quality': f.get('format_note', f.get('resolution', 'Unknown')),
                        'filesize': f.get('filesize', 0),
                        'url': f['url']
                    })
            
            if not formats:
                formats.append({'format_id': 'best', 'ext': 'mp4', 'quality': 'Best Available', 'filesize': 0, 'url': ''})

            return jsonify({
                'title': info.get('title', 'Unknown Title'),
                'thumbnail': info.get('thumbnail', ''),
                'duration': info.get('duration', 0),
                'formats': formats
            })
    except Exception as e:
        error_msg = str(e)
        if "Sign in" in error_msg or "bot" in error_msg:
            return jsonify({'error': 'YouTube ne is video ke liye verification maangi hai. Kripya thodi der baad try karein.'}), 500
        elif "private" in error_msg.lower() or "unavailable" in error_msg.lower():
            return jsonify({'error': 'Yeh video private ya unavailable hai.'}), 500
        else:
            return jsonify({'error': f'Video details fetch nahi ho payi: {error_msg}'}), 500

@app.route('/api/download', methods=['POST'])
def download_video():
    data = request.json
    url = data.get('url')
    format_id = data.get('format_id', 'best')
    
    unique_id = str(uuid.uuid4())
    output_path = os.path.join(DOWNLOAD_FOLDER, f"{unique_id}.%(ext)s")
    
    ydl_opts = {
        'format': f'{format_id}+bestaudio/best' if format_id != 'best' else 'bestvideo+bestaudio/best',
        'outtmpl': output_path,
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'ffmpeg_location': FFMPEG_PATH,
        'retries': 10,
        'fragment_retries': 10,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'no_color': True
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            if not os.path.exists(filename):
                filename = filename.rsplit('.', 1)[0] + '.mp4'
            
            if not os.path.exists(filename):
                return jsonify({'error': 'File download nahi ho payi. Kripya dobara try karein.'}), 500

        return send_file(filename, as_attachment=True, download_name=f"{info.get('title', 'video')}.mp4")
        
    except Exception as e:
        error_msg = str(e)
        if "Requested format is not available" in error_msg:
            return jsonify({'error': 'Yeh format available nahi hai. Kripya koi aur quality chunein.'}), 500
        elif "Sign in" in error_msg or "bot" in error_msg:
            return jsonify({'error': 'YouTube ne verification maangi. Kripya thodi der baad try karein.'}), 500
        else:
            return jsonify({'error': f'Download fail: {error_msg}'}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
