from jwcrypto import jwk
import json

# Generate a 2048-bit RSA key
key = jwk.JWK.generate(kty='RSA', size=2048, alg='RS256', use='sig', kid='grafana-wecom-key')

# 1. Save the Private Key (for your Flask App)
with open('private.pem', 'wb') as f:
    f.write(key.export_to_pem(private_key=True, password=None))

# 2. Save the Public JWKS (for Grafana)
jwks = {"keys": [key.export_public(as_dict=True)]}
with open('jwks.json', 'w') as f:
    json.dump(jwks, f, indent=2)

print("Successfully generated private.pem and jwks.json")