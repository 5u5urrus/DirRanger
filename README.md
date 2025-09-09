# DirRanger  

**DirRanger** is a fast and lightweight crawler that hunts through exposed web directory index pages and extracts every file and folder URL it finds. Perfect for reconnaissance, OSINT, and pentesting engagements where autoindex listings are left open.  

## Features  
- Detects common autoindex formats (Apache, nginx, lighttpd, etc.)  
- Recursively traverses subdirectories to configurable depth  
- Optional retries, deduplication control, and quiet mode  

## Usage  
```bash
python3 dirranger.py http://target.com/path/ --depth 5
````

### Options

* `--depth` — Maximum recursion depth (default: 8)
* `--timeout` — HTTP timeout in seconds (default: 8.0)
* `--quiet` — Suppress warnings/debug output
* `--no-dedupe` — Don’t deduplicate printed URLs (useful on huge trees)

## Example

```bash
python3 dirranger.py http://192.168.56.101/public/ --depth 3
```

Output (clean URLs, one per line):

```
http://192.168.56.101/public/
http://192.168.56.101/public/css/style.css
http://192.168.56.101/public/js/app.js
http://192.168.56.101/public/images/logo.png
```

## Notes

* Only prints URLs (easy to pipe into other tools).
* Designed for reconnaissance
* User-Agent defaults to `DirRanger/1.0`.

## License

MIT License.

## Author

Vahe Demirkhanyan
