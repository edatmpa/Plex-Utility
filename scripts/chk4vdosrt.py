import os
import json
import subprocess


def prompt_for_folder():
    folder_path = input("Please enter the path to the main folder: ").strip('"').strip("'")
    if not os.path.isdir(folder_path):
        print("The provided path is not a valid directory. Please try again.")
        return prompt_for_folder()
    return folder_path


def check_srt_files(folder):
    return (
        "sub" in os.path.basename(folder).lower()
        or any(file.lower().endswith('.srt') for file in os.listdir(folder))
    )


def get_subtitle_streams(mkv_file):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_streams', '-select_streams', 's', mkv_file],
            capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        data = json.loads(result.stdout)
        return data.get('streams', [])
    except Exception as e:
        print(f"Error reading subtitle info for {mkv_file}: {e}")
        return []


def main():
    main_folder = prompt_for_folder()

    folders_with_external_subs = []
    folders_without_external_subs_with_mkv = []
    folders_without_subs = []
    folders_with_subtitles_info = []

    for root, _, files in os.walk(main_folder):
        if root == main_folder:
            continue

        if check_srt_files(root):
            folders_with_external_subs.append(root)
            print(f"SRT file(s) found in {root}, skipping MKV checks.")
            continue

        mkv_files = sorted(f for f in files if f.endswith((
            '.mkv', '.mp4', '.m4v', '.mov',
            '.ts', '.m2ts', '.mts',
            '.avi', '.divx',
            '.wmv', '.asf',
            '.webm',
            '.flv',
            '.mpg', '.mpeg', '.m2v',
            '.vob',
            '.ogv', '.ogg',
            '.3gp', '.3g2',
        )))

        if mkv_files:
            mkv_with_english_subs = False
            subtitles_found = []

            for mkv_file in mkv_files:
                mkv_path = os.path.join(root, mkv_file)
                print(f"Checking {mkv_path} for subtitles...")

                streams = get_subtitle_streams(mkv_path)
                for s in streams:
                    lang = s.get('tags', {}).get('language', 'unknown')
                    codec = s.get('codec_name', 'unknown')
                    subtitles_found.append(f"Subtitle: {codec} [{lang}]")
                    if lang.lower() == 'eng':
                        mkv_with_english_subs = True

            if mkv_with_english_subs:
                folders_without_external_subs_with_mkv.append(root)
                folders_with_subtitles_info.append((root, subtitles_found))
            else:
                folders_without_subs.append(root)
        else:
            folders_without_subs.append(root)

    # Post-process: fix folders whose SRT files are in a subfolder (e.g., Subs/)
    promoted = set()
    external_set = set(folders_with_external_subs)

    for folder in list(folders_without_subs):
        if any(ext.startswith(folder + os.sep) for ext in external_set):
            folders_without_subs.remove(folder)
            folders_with_external_subs.append(folder)
            promoted.add(folder)

    for folder in list(folders_without_external_subs_with_mkv):
        if any(ext.startswith(folder + os.sep) for ext in external_set):
            folders_without_external_subs_with_mkv.remove(folder)
            folders_with_subtitles_info = [(f, s) for f, s in folders_with_subtitles_info if f != folder]
            folders_with_external_subs.append(folder)
            promoted.add(folder)

    # Remove subfolder entries (e.g., Subs/) now represented by their promoted parent
    folders_with_external_subs = [
        f for f in folders_with_external_subs
        if os.path.dirname(f) not in promoted
    ]

    folders_with_external_subs.sort()
    folders_without_external_subs_with_mkv.sort()
    folders_without_subs.sort()

    report_file_a_path = os.path.join(main_folder, 'External Subs.txt')
    with open(report_file_a_path, 'w', encoding='utf-8') as f:
        for folder in folders_with_external_subs:
            f.write(f"{folder}\n")

    report_file_b_path = os.path.join(main_folder, 'Internal Subs.txt')
    with open(report_file_b_path, 'w', encoding='utf-8') as f:
        for folder, _ in folders_with_subtitles_info:
            f.write(f"{folder}\n")

    report_file_c_path = os.path.join(main_folder, 'No Subs.txt')
    with open(report_file_c_path, 'w', encoding='utf-8') as f:
        for folder in folders_without_subs:
            f.write(f"{folder}\n")

    print(f"\nReports generated:\n- {report_file_a_path}\n- {report_file_b_path}\n- {report_file_c_path}")


if __name__ == "__main__":
    main()