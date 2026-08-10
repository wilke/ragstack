import { describe, expect, it } from "vitest";
import { deploymentName } from "./deployment";

// deploymentName reads the SERVED path, which is the only place the gateway
// records which deployment you are on — no endpoint reports it, so a wrong
// parse would put a confident, false name on the Ops page.
describe("deploymentName", () => {
  it("names the deployment from a gateway base path", () => {
    expect(deploymentName("/ragstack/dev/ui/")).toBe("dev");
    expect(deploymentName("/ragstack/lucid/ui")).toBe("lucid");
    expect(deploymentName("/portal/asm/ui/")).toBe("asm"); // prefix is not pinned to "ragstack"
  });

  it("has no name to claim when served at the root or an unrecognised path", () => {
    expect(deploymentName("/")).toBeNull();
    expect(deploymentName("")).toBeNull();
    expect(deploymentName("/ui/")).toBeNull(); // no deployment segment
    expect(deploymentName("/ragstack/dev/")).toBeNull(); // not the UI mount
  });
});
