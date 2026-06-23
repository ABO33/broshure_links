import argparse

from backend.web import run


def main():
    parser = argparse.ArgumentParser(description="Run the Praktis Brochure Linker web app.")
    parser.add_argument(
        "--host",
        default=None,
        help="Address to bind. Default is 0.0.0.0 so other computers on the network can open it.",
    )
    parser.add_argument("--port", type=int, default=None, help="Port to listen on. Default is 5174.")
    args = parser.parse_args()
    run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
