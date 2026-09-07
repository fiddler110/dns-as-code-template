var CF = NewDnsProvider("cloudflare");
var REG = NewRegistrar("none");

D("example.com", REG,
	DnsProvider(CF),
	DefaultTTL(1),
	TXT("_dmarc", "v=DMARC1; p=reject"),
	CAA("@", "issue", "letsencrypt.org"),
	A("@", "203.0.113.5", CF_PROXY_ON),
	MX("@", 50, "amir.mx.cloudflare.net."),
	CNAME("www", "example.com.", CF_PROXY_ON),
	// Unproxied: Jellyfin streaming through the Cloudflare proxy would hit CDN response-size/streaming limits.
	CNAME("jf", "example.com."),
	TXT("@", "v=spf1 include:_spf.mx.cloudflare.net -all"),
);
