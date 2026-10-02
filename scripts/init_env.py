"""Generate local credentials once; never overwrites an existing .env."""
from pathlib import Path
import secrets,string
root=Path(__file__).resolve().parent.parent
path=root/'.env'
if path.exists():
    raise SystemExit('.env existe déjà : aucune modification.')
password=''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(36))
admin=secrets.token_urlsafe(20)
content=(root/'.env.example').read_text().replace('replace_with_32_random_alphanumeric_characters',password).replace('replace_with_a_unique_long_password',admin)
path.write_text(content)
try:path.chmod(0o600)
except OSError:pass
print('Configuration créée. Ne partagez pas le fichier .env.')
print('Administrateur : admin@signal.local')
print('Mot de passe administrateur : '+admin)
print('Démarrer : docker compose up --build -d')
