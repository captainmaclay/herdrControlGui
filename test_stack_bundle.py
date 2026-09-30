import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import stack_bundle_manager as sbm


class TestStackBundleCrypto(unittest.TestCase):
    def test_key_fingerprint(self):
        fp1 = sbm.compute_key_fingerprint("SuperSecret2026!")
        fp2 = sbm.compute_key_fingerprint("SuperSecret2026!")
        fp_diff = sbm.compute_key_fingerprint("DifferentKey2026!")
        self.assertEqual(fp1, fp2)
        self.assertNotEqual(fp1, fp_diff)
        self.assertEqual(len(fp1.split()), 4)

    def test_encrypt_decrypt_roundtrip(self):
        key = "MyMasterSecretKey!#123"
        payload = b"Hello, encrypted Gemini tokens and OmniRoute credentials!"
        enc = sbm._encrypt_data(key, payload)
        self.assertTrue(enc.startswith(sbm.MAGIC_HEADER))
        
        dec = sbm._decrypt_data(key, enc)
        self.assertEqual(dec, payload)

    def test_decrypt_wrong_key_fails(self):
        key = "CorrectKey123"
        wrong_key = "WrongKey456"
        payload = b"Secret data"
        enc = sbm._encrypt_data(key, payload)

        with self.assertRaises(ValueError) as ctx:
            sbm._decrypt_data(wrong_key, enc)
        self.assertIn("Неверный мастер-ключ", str(ctx.exception))

    def test_corrupted_container_fails(self):
        with self.assertRaises(ValueError) as ctx:
            sbm._decrypt_data("Key123", b"CorruptedHeaderAndData")
        self.assertIn("Неверный формат контейнера", str(ctx.exception))


class TestStackBundlePackaging(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_manifest_and_vault_inspection(self):
        # Создаем мини-архив аналогичный бандлу
        bundle_file = self.tmp_dir / "test_bundle.hbin"
        key = "TestMasterKey2026!"
        vault_payload = b"Dummy vault content"
        enc_vault = sbm._encrypt_data(key, vault_payload)

        with zipfile.ZipFile(bundle_file, "w") as zf:
            zf.writestr("manifest.json", '{"version": "1.0", "bundle_type": "herdr_stack_bundle"}')
            zf.writestr("vault.enc", enc_vault)
            zf.writestr("aionui_runtime.tar.gz", b"dummy_tar")

        # Проверяем распаковку
        with zipfile.ZipFile(bundle_file, "r") as zf:
            self.assertIn("vault.enc", zf.namelist())
            self.assertIn("manifest.json", zf.namelist())
            dec = sbm._decrypt_data(key, zf.read("vault.enc"))
            self.assertEqual(dec, vault_payload)


class TestStackBundleUI(unittest.TestCase):
    def test_methods_exist(self):
        import config_app
        self.assertTrue(hasattr(config_app.HerdrConfigApp, "on_build_stack_bundle_click"))
        self.assertTrue(hasattr(config_app.HerdrConfigApp, "on_install_stack_bundle_click"))


if __name__ == "__main__":
    unittest.main()
