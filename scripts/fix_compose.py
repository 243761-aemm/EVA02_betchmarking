import re

with open('docker-compose.yml', 'r', encoding='utf-8') as f:
    text = f.read()

# remove version line
text = re.sub(r'version:\s*["\']3\.9["\']\s*\r?\n', '', text)

with open('docker-compose.yml', 'w', encoding='utf-8') as f:
    f.write(text)

print("Version line removed successfully.")

