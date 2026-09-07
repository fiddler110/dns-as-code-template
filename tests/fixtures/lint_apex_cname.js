var CF = NewDnsProvider("cloudflare");
var REG = NewRegistrar("none");

D("example.com", REG,
	DnsProvider(CF),
	DefaultTTL(1),
	CNAME("@", "example.com."),
);
