// 在个人 tailnet 内转发一个回环 SSH 端口，只允许节点所属用户接入。
package main

import (
	"context"
	"flag"
	"io"
	"log"
	"net"
	"os"
	"time"

	"tailscale.com/tsnet"
)

func main() {
	state := flag.String("state", "", "私有节点状态目录")
	hostname := flag.String("hostname", "personal-gpu", "tailnet 内的节点名称")
	flag.Parse()
	if *state == "" {
		log.Fatal("必须指定 -state")
	}
	if err := os.MkdirAll(*state, 0700); err != nil {
		log.Fatal(err)
	}

	server := &tsnet.Server{Dir: *state, Hostname: *hostname, UserLogf: log.Printf}
	defer server.Close()
	status, err := server.Up(context.Background())
	if err != nil {
		log.Fatal(err)
	}
	if status.Self == nil || status.Self.UserID == 0 {
		log.Fatal("缺少个人节点身份")
	}
	owner := status.Self.UserID
	client, err := server.LocalClient()
	if err != nil {
		log.Fatal(err)
	}
	listener, err := server.Listen("tcp", ":22")
	if err != nil {
		log.Fatal(err)
	}
	defer listener.Close()
	log.Printf("个人 SSH 入口已启动")

	slots := make(chan struct{}, 32)
	for {
		connection, err := listener.Accept()
		if err != nil {
			log.Fatal(err)
		}
		select {
		case slots <- struct{}{}:
		default:
			connection.Close()
			continue
		}
		go func(peer net.Conn) {
			defer func() { <-slots }()
			defer peer.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			identity, err := client.WhoIs(ctx, peer.RemoteAddr().String())
			cancel()
			if err != nil || identity == nil || identity.UserProfile == nil || identity.UserProfile.ID != owner {
				log.Print("拒绝无法验证为节点所有者的连接")
				return
			}

			dialer := net.Dialer{Timeout: 5 * time.Second}
			backend, err := dialer.Dial("tcp", "127.0.0.1:22222")
			if err != nil {
				log.Printf("连接个人 SSH 服务失败：%v", err)
				return
			}
			defer backend.Close()

			done := make(chan struct{})
			go func() {
				defer close(done)
				io.Copy(backend, peer)
				if tcp, ok := backend.(*net.TCPConn); ok {
					tcp.CloseWrite()
				}
			}()
			io.Copy(peer, backend)
			peer.Close()
			backend.Close()
			<-done
		}(connection)
	}
}
