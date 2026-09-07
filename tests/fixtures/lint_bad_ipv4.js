var CF = NewDnsProvider("cloudflare");
var REG = NewRegistrar("none");

D("example.com", REG,
	DnsProvider(CF),
	DefaultTTL(1),
	A("bad", "999.1.1.1", CF_PROXY_ON),
);
