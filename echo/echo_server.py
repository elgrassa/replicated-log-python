import socket, sys, threading

def handle(conn, addr):
    with conn:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            conn.sendall(data)

def main(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(5)
    print(f"Echo server listening on 0.0.0.0:{port}")
    try:
        while True:
            conn, addr = s.accept()
            t = threading.Thread(target=handle, args=(conn, addr), daemon=True)
            t.start()
    finally:
        s.close()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9009
    main(port)
