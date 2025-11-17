import socket, sys

def main(host, port, message="hello"):
    with socket.create_connection((host, port), timeout=5) as s:
        s.sendall(message.encode())
        data = s.recv(4096)
        print(data.decode())

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python echo_client.py <host> <port> [message]")
        raise SystemExit(2)
    host = sys.argv[1]
    port = int(sys.argv[2])
    msg = sys.argv[3] if len(sys.argv) > 3 else "hello"
    main(host, port, msg)
