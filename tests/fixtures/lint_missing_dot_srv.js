var CF = NewDnsProvider("cloudflare");
var REG = NewRegistrar("none");

D("example.com", REG,
	DnsProvider(CF),
	DefaultTTL(1),
	SRV("_service._tcp", 10, 60, 5060, "target.example.com"),
);
