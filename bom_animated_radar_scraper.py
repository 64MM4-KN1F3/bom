#!/usr/bin/env python
import os
import sys
import requests
import argparse
import re
import json
from PIL import Image, ImageSequence
from io import BytesIO

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
    """
    try:
        gifs = [Image.open(path) for path in gif_paths]
        
        if not gifs:
            return False

        # Ensure they have the same number of frames or handle mismatch
        n_frames = min(g.n_frames for g in gifs)
        
        frames = []
        header_height = 16
        
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
    parser.add_argument("--dev", action="store_true", help="Run in development mode (skip scraping, just stack existing temp files if they exist).")
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
            
            if not args.dev:
                print(f"  Generating {range_key} radar GIF...")
                success = create_animated_radar(url, temp_filename)
                if not success:
                    print(f"  Failed to create GIF for {range_key}, skipping this view.")
                    temp_gifs.pop() # Remove failed file from list
            else:
                if not os.path.exists(temp_filename):
                    print(f"  Dev mode: {temp_filename} missing, cannot proceed with this view.")
                    temp_gifs.pop()

        if temp_gifs:
            output_filename = f"{friendly_name}.gif"
            print(f"  Stacking {len(temp_gifs)} GIFs into {output_filename}...")
            stack_success = stack_animated_gifs(temp_gifs, output_filename)
            
            if stack_success:
                # Cleanup temp files
                if not args.dev:
                    for temp in temp_gifs:
                        if os.path.exists(temp):
                            os.remove(temp)
            else:
                print("  Stacking failed.")
        else:
            print("  No valid GIFs generated to stack.")