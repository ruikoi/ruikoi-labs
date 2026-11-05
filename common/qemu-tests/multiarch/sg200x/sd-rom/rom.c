/* SPDX-License-Identifier: MIT */
/* Experimental replacement ROM, not a dump of the vendor BootROM.
 * Standard SDHCI PIO -> FAT16 FIP -> unmodified RV64 FSBL.
 * No DDR-training, PLL, security or vendor SD PHY success stubs.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long u64;
#define SD 0x04310000UL
#define UART 0x04140000UL
static u32 partition, fat_start, root_start, data_start;
static u32 sectors_per_cluster, root_entries, file_cluster, file_size;
static u32 high_capacity, boot_source;
static u8 sector_buf[512];
static u8 fat_buf[512];

void *memcpy(void *dest, const void *src, u64 length)
{
    u8 *d=dest; const u8 *s=src;
    while (length--) *d++=*s++;
    return dest;
}

static u32 r32(u64 p) { return *(volatile u32 *)p; }
static u16 r16(u64 p) { return *(volatile u16 *)p; }
static void w32(u64 p, u32 v) { *(volatile u32 *)p = v; }
static void w16(u64 p, u16 v) { *(volatile u16 *)p = v; }
static void w8(u64 p, u8 v) { *(volatile u8 *)p = v; }
static u32 le32(const u8 *p)
{
    return p[0] | (u32)p[1]<<8 | (u32)p[2]<<16 | (u32)p[3]<<24;
}
static u16 le16(const u8 *p) { return p[0] | (u16)p[1]<<8; }
static u64 ticks(void) { u64 v; __asm__ volatile("rdtime %0":"=r"(v)); return v; }
static void puts(const char *p)
{
    while (*p) {
        while (!(r32(UART+20) & 0x20)) { }
        w32(UART, *p++);
    }
}
static void hex(u64 v)
{
    char s[19] = "0x0000000000000000";
    for (int i=17; i>=2; --i) { s[i]="0123456789abcdef"[v&15]; v>>=4; }
    puts(s);
}
static void fail(const char *why)
{
    puts("ROM STOP: "); puts(why); puts("\r\n");
    for (;;) __asm__ volatile("wfi");
}
static int wait_status(u16 bit)
{
    u64 end=ticks()+25000000;
    while (ticks()<end) {
        u16 status=r16(SD+0x30);
        if (status & 0x8000) return -1;
        if (status & bit) return 0;
    }
    return -1;
}
static int command(u32 number, u32 arg, u16 flags)
{
    w32(SD+0x30,0xffffffff);
    w32(SD+8,arg);
    w16(SD+0xe,(number<<8)|flags);
    if (wait_status(1)) {
        puts("SD CMD "); hex(number); puts(" status "); hex(r32(SD+0x30)); puts("\r\n");
        return -1;
    }
    w16(SD+0x30,1);
    return 0;
}
static int sd_init(void)
{
    w8(SD+0x2f,1);
    w8(SD+0x29,0xf);
    w16(SD+0x2c,0x107);
    w8(SD+0x2e,0xe);
    w32(SD+0x34,0xffffffff);
    w32(SD+0x38,0);
    if (command(0,0,0) || command(8,0x1aa,0x1a)) return -1;
    u64 end=ticks()+25000000;
    do {
        if (command(55,0,0x1a) || command(41,0x40ff8000,2)) return -1;
        if (r32(SD+0x10)&0x80000000) break;
    } while (ticks()<end);
    u32 ocr=r32(SD+0x10);
    if (!(ocr&0x80000000)) return -1;
    high_capacity=!!(ocr&0x40000000);
    if (command(2,0,9) || command(3,0,0x1a)) return -1;
    u32 rca=r32(SD+0x10)&0xffff0000;
    if (command(7,rca,0x1b) || command(16,512,0x1a)) return -1;
    return 0;
}
static int read_sector(u32 lba, u8 *dest)
{
    w16(SD+4,512);
    w16(SD+6,1);
    w16(SD+0xc,0x10);
    if (command(17, high_capacity ? lba : lba*512, 0x3a) || wait_status(0x20)) return -1;
    for (int i=0; i<128; ++i) {
        u32 word=r32(SD+0x20);
        for (int b=0; b<4; ++b) dest[4*i+b]=word>>(b*8);
    }
    if (wait_status(2)) return -1;
    w16(SD+0x30,0xffff);
    return 0;
}
static int find_fip(void)
{
    if (read_sector(0,sector_buf) || le16(sector_buf+510)!=0xaa55) return -1;
    partition=le32(sector_buf+454);
    if (read_sector(partition,sector_buf) || le16(sector_buf+11)!=512) return -1;
    sectors_per_cluster=sector_buf[13];
    if (!sectors_per_cluster) return -1;
    root_entries=le16(sector_buf+17);
    u32 fat_size=le16(sector_buf+22);
    if (!root_entries || !fat_size) return -1; /* FAT16 only */
    fat_start=partition+le16(sector_buf+14);
    root_start=fat_start+sector_buf[16]*fat_size;
    data_start=root_start+(root_entries*32+511)/512;
    for (u32 index=0; index<root_entries; ++index) {
        if (!(index&15) && read_sector(root_start+index/16,sector_buf)) return -1;
        u8 *entry=sector_buf+(index&15)*32;
        const char *wanted="FIP     BIN";
        int equal=1;
        for (int i=0;i<11;++i) if (entry[i]!=wanted[i]) equal=0;
        if (equal && !(entry[11]&0x18)) {
            file_cluster=le16(entry+26); file_size=le32(entry+28); return 0;
        }
    }
    return -1;
}
int rom_load_image(void *dest, u32 offset, u64 length, int retry)
{
    (void)retry;
    if (offset>file_size || length>file_size-offset) return -1;
    u32 cluster=file_cluster;
    u32 skip=offset/(512*sectors_per_cluster);
    for (u32 n=0;n<skip;++n) {
        if (cluster<2 || cluster>=0xfff8 || read_sector(fat_start+cluster/256,fat_buf)) return -1;
        cluster=le16(fat_buf+(cluster%256)*2);
    }
    u32 inside=offset%(512*sectors_per_cluster);
    u8 *out=dest;
    while (length) {
        if (cluster<2 || cluster>=0xfff8) return -1;
        if (read_sector(data_start+(cluster-2)*sectors_per_cluster+inside/512,sector_buf)) return -1;
        u32 part=512-inside%512;
        if (part>length) part=length;
        for (u32 i=0;i<part;++i) out[i]=sector_buf[inside%512+i];
        out+=part; length-=part; inside+=part;
        if (inside==512*sectors_per_cluster && length) {
            if (read_sector(fat_start+cluster/256,fat_buf)) return -1;
            cluster=le16(fat_buf+(cluster%256)*2); inside=0;
        }
    }
    return 0;
}
u32 rom_image_crc(const void *buf, int length)
{
    const u8 *p=buf; u16 crc=0;
    if (length<0) return 0;
    while (length--) {
        crc^=(u16)*p++<<8;
        for (int i=0;i<8;++i) crc=(crc&0x8000) ? (crc<<1)^0x1021 : crc<<1;
    }
    return 0xcafe0000U|crc;
}
u32 rom_get_boot_src(void) { return boot_source; }
void rom_set_boot_src(u32 value) { boot_source=value; }
int rom_get_retries(void) { return 1; }
int rom_flash_init(void) { return -1; }
int rom_verify_rsa(void) { return -1; }
int rom_aes_decrypt(void) { return -1; }
void rom_main(void)
{
    /* Both initial boot and subsequent system_reset initialize the UART. */
    w32(UART+12,0x80); w32(UART,14); w32(UART+4,0);
    w32(UART+12,3); w32(UART+8,7);
    boot_source=0xcea0;
    puts("SG200X experimental SD ROM\r\n");
    if (sd_init()) fail("SD initialization");
    if (find_fip()) fail("FAT16 FIP.BIN lookup");
    u8 *param=(u8 *)0x0c039000;
    if (rom_load_image(param,0,4096,0)) fail("param1 read");
    if (le32(param)!=0x4c425643 || le32(param+4)!=0x000a3130) fail("FIP magic");
    if (rom_image_crc(param+16,2048-16)!=le32(param+12)) fail("param1 CRC");
    if (le32(param+0xb4)&0x3c) fail("secure/encrypted FIP unsupported");
    u32 offset=4096+le32(param+0xc4), size=le32(param+0xd8);
    if (size<32 || size>0x37000) fail("BL2 size");
    if (rom_load_image((void *)0x0c000000,offset,size,0)) fail("BL2 read");
    if (rom_image_crc((void *)0x0c000000,size)!=le32(param+0xd4)) fail("BL2 CRC");
    puts("ROM: verified FIP, entering unmodified FSBL at 0x0c000020\r\n");
    __asm__ volatile("fence.i" ::: "memory");
    ((void (*)(void))0x0c000020)();
    fail("FSBL returned");
}
