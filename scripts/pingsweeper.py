import json, socket, time

MCAST_GRP = "239.255.255.250"
MCAST_PORT = 4001
LISTEN_PORT = 4002

msg = {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}
payload = json.dumps(msg).encode("utf-8")

# Listen for replies on UDP 4002
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("", LISTEN_PORT))
sock.settimeout(2)

# Send scan packet to multicast group
send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
send.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
send.sendto(payload, (MCAST_GRP, MCAST_PORT))

print("Sent scan; listening for replies on UDP 4002...\n")

start = time.time()
seen = set()
while time.time() - start < 5:
    try:
        data, addr = sock.recvfrom(4096)
        if addr[0] in seen:
            continue
        seen.add(addr[0])
        print(addr[0], "->", data.decode("utf-8", errors="replace"))
    except socket.timeout:
        break