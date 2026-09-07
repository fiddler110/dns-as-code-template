var CF = NewDnsProvider("cloudflare");
var REG = NewRegistrar("none");

D("example.com", REG,
	DnsProvider(CF),
	DefaultTTL(1),
	TXT("_acme-challenge", "token", TTL(0)),
);
