var CF = NewDnsProvider("cloudflare");
var REG = NewRegistrar("none");

D("example.com", REG,
	DnsProvider(CF),
	DefaultTTL(1),
	A("@", "203.0.113.5", CF_PROXY_ON),
	A("noComma", "1.2.3.4")
);
