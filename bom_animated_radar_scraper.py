#!/usr/bin/env python
import os
import sys
import requests
import argparse
import re
import json
from PIL import Image, ImageSequence, ImageDraw, ImageFont
from io import BytesIO

import datetime

def get_image_urls(page_url, base_url="https://reg.bom.gov.au"):
    """
    Fetches the radar loop page and extracts the background image and list of radar frame URLs.
    Returns a dictionary with URLs: background, topography, locations, range, frames.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36"
        }
        # print(f"Fetching page: {page_url}")
        response = requests.get(page_url, headers=headers, timeout=10)
        response.raise_for_status()
        content = response.text

        product_id = re.search(r'(IDR\d+)', page_url).group(1)
        
        # Define URLs for all potential layers
        urls = {
            'background': f"{base_url}/products/radar_transparencies/{product_id}.background.png",
            'topography': f"{base_url}/products/radar_transparencies/{product_id}.topography.png",
            'locations': f"{base_url}/products/radar_transparencies/{product_id}.locations.png",
            'range': f"{base_url}/products/radar_transparencies/{product_id}.range.png",
            'frames': []
        }
        
        # Extract frame URLs from JS variable: theImageNames[i] = "/radar/..."
        frame_matches = re.findall(r'theImageNames\[\d+\]\s*=\s*"([^"]+)"', content)
        if not frame_matches:
            print(f"Error: Could not find radar frames in {page_url}")
            return None
            
        urls['frames'] = [f"{base_url}{url}" for url in frame_matches]
        
        return urls

    except Exception as e:
        print(f"Error fetching details from {page_url}: {e}")
        return None

def fetch_image(url, name="image"):
    """Fetches an image from a URL and returns a PIL Image object (RGBA), or None if failed."""
    try:
        # print(f"Downloading {name}: {url}")
        resp = requests.get(url)
        if resp.status_code == 200:
            return Image.open(BytesIO(resp.content)).convert("RGBA")
        else:
            # print(f"Failed to fetch {name} (Status {resp.status_code})")
            return None
    except Exception as e:
        print(f"Error downloading {name}: {e}")
        return None

def create_animated_radar(page_url, output_gif_path, crop=False):
    """
    Downloads frames and layers, composites them, and saves as an animated GIF.
    Also saves a sidecar .json file with the timestamp of the last frame.
    Returns True on success.
    """
    urls = get_image_urls(page_url)
    if not urls or not urls['frames']:
        return False

    try:
        # Download static layers
        # print("Downloading static layers...")
        background = fetch_image(urls['background'], "background")
        topography = fetch_image(urls['topography'], "topography")
        locations = fetch_image(urls['locations'], "locations")
        range_overlay = fetch_image(urls['range'], "range")
        
        if not background:
            print("Critical: Background not found. Using black placeholder.")
            background = Image.new('RGBA', (512, 512), (0, 0, 0, 255))

        frames = []
        for i, frame_url in enumerate(urls['frames']):
            # print(f"Downloading frame {i+1}/{len(urls['frames'])}: {frame_url}")
            radar_layer = fetch_image(frame_url, f"frame {i+1}")
            
            if not radar_layer:
                print(f"Skipping failed frame: {frame_url}")
                continue

            # Composite: Background -> Topography -> Radar -> Locations -> Range
            composite = Image.new('RGBA', background.size)
            composite.paste(background, (0,0))
            
            if topography:
                composite.alpha_composite(topography)
                
            if radar_layer:
                composite.alpha_composite(radar_layer)
                
            if locations:
                composite.alpha_composite(locations)
                
            if range_overlay:
                composite.alpha_composite(range_overlay)
            
            # Convert to RGB for GIF
            frame_img = composite.convert("RGB").quantize(colors=256, method=Image.MAXCOVERAGE, dither=Image.NONE)
            frames.append(frame_img)

        if frames:
            # Save as animated GIF
            frames[0].save(
                output_gif_path,
                save_all=True,
                append_images=frames[1:],
                duration=500,
                loop=0
            )
            
            # Extract timestamp from last frame URL
            # Format: .../IDR714.T.202511271009.png
            last_frame_url = urls['frames'][-1]
            match = re.search(r'\.(\d{12})\.png$', last_frame_url)
            if match:
                timestamp_str = match.group(1)
                # Save to sidecar JSON
                sidecar_path = f"{output_gif_path}.json"
                with open(sidecar_path, 'w') as f:
                    json.dump({"timestamp": timestamp_str}, f)
            
            # print(f"Saved animated GIF to {output_gif_path}")
            return True
        else:
            return False

    except Exception as e:
        print(f"Error creating animated radar for {page_url}: {e}")
        return False

def stack_animated_gifs(gif_paths, output_path):
    """
    Stacks multiple animated GIFs vertically.
    Moves the top 16px header from the first GIF to the bottom of the stack.
    Checks for outdated data and overlays a warning if needed.
    """
    try:
        gifs = [Image.open(path) for path in gif_paths]
        
        if not gifs:
            return False

        # Check for outdated data
        is_outdated = False
        current_utc = datetime.datetime.now(datetime.timezone.utc)
        
        for path in gif_paths:
            sidecar_path = f"{path}.json"
            if os.path.exists(sidecar_path):
                try:
                    with open(sidecar_path, 'r') as f:
                        data = json.load(f)
                        timestamp_str = data.get("timestamp")
                        if timestamp_str:
                            # Parse YYYYMMDDHHMM
                            # BOM times are UTC
                            dt = datetime.datetime.strptime(timestamp_str, "%Y%m%d%H%M").replace(tzinfo=datetime.timezone.utc)
                            age = current_utc - dt
                            if age > datetime.timedelta(minutes=15):
                                is_outdated = True
                                break
                except Exception as e:
                    print(f"Warning: Could not check timestamp for {path}: {e}")

        # Ensure they have the same number of frames or handle mismatch
        n_frames = min(g.n_frames for g in gifs)
        
        frames = []
        header_height = 16
        
        # Load font for warning
        font = None
        # List of fonts to try (Linux, macOS, Windows)
        font_candidates = [
            "DejaVuSans.ttf",
            "FreeSans.ttf",
            "Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial.ttf"
        ]
        
        for font_name in font_candidates:
            try:
                font = ImageFont.truetype(font_name, 20)
                break
            except IOError:
                continue
        
        if font is None:
            font = ImageFont.load_default()

        for i in range(n_frames):
            current_frames = []
            for g in gifs:
                g.seek(i)
                current_frames.append(g.convert("RGBA"))
            
            # Extract header from the first image (smallest radar)
            # We assume the header is identical or we only care about the one from the first radar
            header = current_frames[0].crop((0, 0, current_frames[0].width, header_height))
            
            # Crop header from all images
            cropped_frames = [img.crop((0, header_height, img.width, img.height)) for img in current_frames]
            
            # Calculate dimensions
            total_width = max(img.width for img in cropped_frames)
            
            divider_height = 1
            num_dividers = max(0, len(cropped_frames) - 1)
            
            # Total height = sum of cropped heights + dividers + header height
            total_height = sum(img.height for img in cropped_frames) + (num_dividers * divider_height) + header_height
            
            # Create new canvas
            new_img = Image.new('RGBA', (total_width, total_height), (0, 0, 0, 255))
            
            current_y = 0
            for idx, img in enumerate(cropped_frames):
                # Center image if widths differ (unlikely for BOM radars but good practice)
                x_offset = (total_width - img.width) // 2
                new_img.paste(img, (x_offset, current_y))
                current_y += img.height
                
                # Add divider after each image except the last one
                if idx < len(cropped_frames) - 1:
                    current_y += divider_height
            
            # Paste header at the bottom
            new_img.paste(header, (0, current_y))
            
            # Overlay warning if outdated
            if is_outdated:
                draw = ImageDraw.Draw(new_img)
                text = "radar capture outdated" # Removed emoji
                
                # Calculate text size
                if hasattr(draw, "textbbox"):
                    bbox = draw.textbbox((0, 0), text, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                else:
                    text_width, text_height = draw.textsize(text, font=font)
                
                # Symbol dimensions
                symbol_size = 18
                padding = 3
                box_padding = 4 # Padding for the background box
                
                total_content_width = symbol_size + padding + text_width
                total_content_height = max(symbol_size, text_height)
                
                start_x = (total_width - total_content_width) // 2
                y_pos = 3 # Moved down slightly to ensure top border is visible
                
                # Draw Background Box
                box_left = start_x - box_padding
                box_top = y_pos - box_padding + 2
                box_right = start_x + total_content_width + box_padding
                box_bottom = y_pos + total_content_height + (box_padding / 2)
                
                draw.rectangle(
                    [box_left, box_top, box_right, box_bottom],
                    fill=(255, 255, 255, 255),
                    outline=(255, 0, 0, 255),
                    width=1
                )
                
                # Draw Symbol (Red Triangle with Exclamation)
                triangle_points = [
                    (start_x + symbol_size // 2, y_pos), # Top
                    (start_x, y_pos + symbol_size),      # Bottom Left
                    (start_x + symbol_size, y_pos + symbol_size) # Bottom Right
                ]
                draw.polygon(triangle_points, fill=(255, 0, 0, 255))
                
                # Draw Exclamation Mark (White)
                # Simple approximation: a line and a dot
                excl_x = start_x + symbol_size // 2
                draw.line([(excl_x, y_pos + 5), (excl_x, y_pos + 12)], fill=(255, 255, 255, 255), width=3)
                draw.point((excl_x, y_pos + 15), fill=(255, 255, 255, 255))
                # Make the dot a bit bigger
                draw.rectangle([excl_x-1, y_pos+15, excl_x+1, y_pos+17], fill=(255, 255, 255, 255))

                # Draw Text (Aliased)
                text_x = start_x + symbol_size + padding
                
                # Create a 1-bit mask for the text to ensure no anti-aliasing
                # We use the bbox dimensions to ensure we capture descenders
                mask = Image.new('1', (text_width, text_height), 0)
                mask_draw = ImageDraw.Draw(mask)
                
                # Draw text onto mask, offsetting by the bbox top-left to capture all ink
                # bbox is (left, top, right, bottom) relative to drawing position (0,0)
                # So we draw at (-left, -top)
                if hasattr(draw, "textbbox"):
                     mask_draw.text((-bbox[0], -bbox[1]), text, font=font, fill=1)
                else:
                     mask_draw.text((0, 0), text, font=font, fill=1)
                
                # Create a solid red image to paste
                color_img = Image.new('RGBA', (text_width, text_height), (255, 0, 0, 255))
                
                # Paste onto the main image using the mask
                new_img.paste(color_img, (text_x, y_pos), mask)

            # Quantize
            frame_img = new_img.convert("RGB").quantize(colors=256, method=Image.MAXCOVERAGE, dither=Image.NONE)
            frames.append(frame_img)

        if frames:
            frames[0].save(
                output_path,
                save_all=True,
                append_images=frames[1:],
                duration=gifs[0].info.get('duration', 500),
                loop=0
            )
            print(f"Successfully created stacked GIF: {output_path}")
            return True
        return False

    except Exception as e:
        print(f"Error stacking animated GIFs: {e}")
        return False

def parse_range(range_str):
    """Extracts integer range from string like '64km'."""
    match = re.search(r'(\d+)', range_str)
    return int(match.group(1)) if match else 9999

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape and process BOM radar animated images for multiple cities.")
    parser.add_argument("--dev-capture", action="store_true", help="Capture mode: Generate temp files but do not stack or delete them.")
    parser.add_argument("--dev-process", action="store_true", help="Process mode: Stack existing temp files but do not generate new ones or delete them.")
    args = parser.parse_args()

    json_path = "bom_radars.json"
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found.")
        sys.exit(1)

    with open(json_path, 'r', encoding='utf-8-sig') as f:
        cities = json.load(f)

    target_cities = [
        "Sydney", "Melbourne", "Brisbane", "Perth", 
        "Adelaide", "Hobart", "Darwin", "Canberra"
    ]

    for city in cities:
        city_name = city.get("City", "unknown")
        if city_name not in target_cities:
            continue

        friendly_name = city.get("FriendlyName", "unknown")
        print(f"\nProcessing {city_name}...")
        
        views = city.get("Views", {})
        if not views:
            print("No views found, skipping.")
            continue

        # Sort views by range (smallest to largest)
        sorted_views = sorted(views.items(), key=lambda item: parse_range(item[0]))
        
        temp_gifs = []
        
        for range_key, url in sorted_views:
            temp_filename = f"temp_{friendly_name}_{range_key}.gif"
            temp_gifs.append(temp_filename)
            
            # Generation Logic
            should_generate = True
            if args.dev_process:
                should_generate = False
            
            if should_generate:
                print(f"  Generating {range_key} radar GIF...")
                success = create_animated_radar(url, temp_filename)
                if not success:
                    print(f"  Failed to create GIF for {range_key}, skipping this view.")
                    temp_gifs.pop() # Remove failed file from list

        # Stacking Logic
        should_stack = True
        if args.dev_capture:
            should_stack = False

        if should_stack:
            if temp_gifs:
                output_filename = f"{friendly_name}.gif"
                print(f"  Stacking {len(temp_gifs)} GIFs into {output_filename}...")
                stack_success = stack_animated_gifs(temp_gifs, output_filename)
                
                if stack_success:
                    # Cleanup temp files
                    # Only cleanup if NOT in any dev mode
                    if not (args.dev_capture or args.dev_process):
                        for temp in temp_gifs:
                            if os.path.exists(temp):
                                os.remove(temp)
                            # Cleanup sidecar json
                            sidecar = f"{temp}.json"
                            if os.path.exists(sidecar):
                                os.remove(sidecar)
                else:
                    print("  Stacking failed.")
            else:
                print("  No valid GIFs generated to stack.")
        else:
            print("  Skipping stacking (Capture Mode).")