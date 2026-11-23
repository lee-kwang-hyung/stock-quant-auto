"""GitHub 없이도 내 PC에서 더블클릭 한 번으로 최신 시세 분석 후 앱 화면을 바로 띄우는 로컬 실행기."""
import functools
import http.server
import pathlib
import socketserver
import threading
import time
import webbrowser

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
PORT = 8765


def main():
    print("==============================================")
    print("  나만의 주식 비서 v3.5 — 최신 신뢰 시세 스캔 중...")
    print("==============================================")
    try:
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from stock_analyzer import run_analysis
        run_analysis(ROOT / "config/watchlist.json", ROOT / "reports", ROOT / "data", intraday=True)
    except Exception as e:
        print(f"[안내] 실시간 스캔 중 일부 오류({e}) — 기존 최신 데이터로 앱을 엽니다.")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DOCS))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), handler) as httpd:
        url = f"http://127.0.0.1:{PORT}/index.html"
        print(f"\n[준비 완료] 브라우저에서 앱을 엽니다: {url}")
        print("이 창을 닫으면 앱이 종료됩니다.\n")
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n종료합니다.")


if __name__ == "__main__":
    main()
