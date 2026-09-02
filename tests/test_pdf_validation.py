from app.utils.security import looks_like_pdf, sha256_bytes


def test_pdf_magic_accepted():
    data = b"%PDF-1.7\n" + b"0" * 3000
    assert looks_like_pdf("application/pdf", data[:8], 2048, len(data))


def test_html_rejected():
    data = b"<!DOCTYPE html><html>login</html>"
    assert not looks_like_pdf("text/html", data[:16], 10, len(data))


def test_too_small_rejected():
    data = b"%PDF-1.4 tiny"
    assert not looks_like_pdf("application/pdf", data, 2048, len(data))


def test_sha256_stable():
    assert sha256_bytes(b"abc") == sha256_bytes(b"abc")
    assert sha256_bytes(b"abc") != sha256_bytes(b"abd")
