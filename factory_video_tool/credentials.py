"""Explicit OS credential backend; never fall back to plaintext files."""
import platform

class CredentialStore:
    service='P009-FactoryVideo-Douyin'
    def _backend(self):
        if platform.system()=='Darwin':
            from keyring.backends.macOS import Keyring
        elif platform.system()=='Windows':
            from keyring.backends.Windows import WinVaultKeyring as Keyring
        else:raise RuntimeError('仅支持macOS钥匙串或Windows凭据管理器')
        return Keyring()
    def save(self,reference,token):
        if not token.strip():raise ValueError('授权信息为空')
        self._backend().set_password(self.service,reference,token.strip())
    def get(self,reference):
        return self._backend().get_password(self.service,reference)
