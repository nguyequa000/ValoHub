using Replay.Encoding.Net;
using Replay.Models.Net;

namespace Replay.Encoding.Tests.Net;

public class NetGuidCacheTests
{
    [Test]
    public void AddExportGroup_StoresGroupByPathAndIndex()
    {
        var cache = new NetGuidCache();
        var group = CreateGroup();

        cache.AddExportGroup(group);

        Assert.Multiple(() =>
        {
            Assert.That(cache.ExportGroupsByPath[group.PathName], Is.SameAs(group));
            Assert.That(cache.GetExportGroup(group.PathNameIndex), Is.SameAs(group));
        });
    }

    [Test]
    public void AddExportGroup_ExpandsExistingGroupAndPreservesExports()
    {
        var cache = new NetGuidCache();
        var existingExport = new NetFieldExport
        {
            Handle = 1,
            CompatibleChecksum = 17,
            Name = "ExistingField",
        };
        var existingGroup = CreateGroup();
        existingGroup.NetFieldExports[1] = existingExport;
        cache.AddExportGroup(existingGroup);

        var expandedGroup = cache.AddExportGroup(new NetFieldExportGroup
        {
            PathName = existingGroup.PathName,
            PathNameIndex = existingGroup.PathNameIndex,
            NetFieldExports = new NetFieldExport?[4],
        });

        Assert.Multiple(() =>
        {
            Assert.That(expandedGroup.NetFieldExportsLength, Is.EqualTo(4));
            Assert.That(expandedGroup.NetFieldExports[1], Is.SameAs(existingExport));
            Assert.That(cache.ExportGroupsByPath[existingGroup.PathName], Is.SameAs(expandedGroup));
            Assert.That(cache.GetExportGroup(existingGroup.PathNameIndex), Is.SameAs(expandedGroup));
        });
    }

    [Test]
    public void SetNetGuidPath_StoresPath()
    {
        var cache = new NetGuidCache();

        cache.SetNetGuidPath(17, "/Game/Test.Test_C");

        Assert.Multiple(() =>
        {
            Assert.That(cache.TryGetPath(17, out var pathName), Is.True);
            Assert.That(pathName, Is.EqualTo("/Game/Test.Test_C"));
        });
    }

    [Test]
    public void TryGetFullPath_JoinsOutersLikeGetPathName()
    {
        var cache = new NetGuidCache();
        cache.SetNetGuidPath(3, "/Game/Weapons/Pistol");
        cache.SetNetGuidPath(5, "Pistol_C", new NetworkGuid(3));
        cache.SetNetGuidPath(7, "FiringState", new NetworkGuid(5));
        cache.SetNetGuidPath(9, "Inner", new NetworkGuid(7));

        Assert.Multiple(() =>
        {
            Assert.That(cache.TryGetFullPath(3, out var package), Is.True);
            Assert.That(package, Is.EqualTo("/Game/Weapons/Pistol"));
            Assert.That(cache.TryGetFullPath(5, out var asset), Is.True);
            Assert.That(asset, Is.EqualTo("/Game/Weapons/Pistol.Pistol_C"));
            Assert.That(cache.TryGetFullPath(9, out var inner), Is.True);
            Assert.That(inner, Is.EqualTo("/Game/Weapons/Pistol.Pistol_C:FiringState.Inner"));
        });
    }

    [Test]
    public void TryGetFullPath_FailsWhenAnOuterIsUnknown()
    {
        var cache = new NetGuidCache();
        cache.SetNetGuidPath(5, "Pistol_C", new NetworkGuid(3));

        Assert.That(cache.TryGetFullPath(5, out var missingPath), Is.False);
        Assert.That(missingPath, Is.Null);

        cache.SetNetGuidPath(3, "/Game/Weapons/Pistol");

        Assert.That(cache.TryGetFullPath(5, out var fullPath), Is.True);
        Assert.That(fullPath, Is.EqualTo("/Game/Weapons/Pistol.Pistol_C"));
    }

    [TestCase(false)]
    [TestCase(true)]
    public void TryGetFullPath_CyclicOutersReturnFalseAndCanBeRepaired(bool selfCycle)
    {
        var cache = new NetGuidCache();
        cache.SetNetGuidPath(3, "Root", new NetworkGuid(selfCycle ? 3u : 5u));
        cache.SetNetGuidPath(5, "Child", new NetworkGuid(3));

        Assert.That(cache.TryGetFullPath(5, out var cyclicPath), Is.False);
        Assert.That(cyclicPath, Is.Null);

        cache.SetNetGuidPath(3, "Root");

        Assert.That(cache.TryGetFullPath(5, out var repairedPath), Is.True);
        Assert.That(repairedPath, Is.EqualTo("Root.Child"));
    }

    [TestCase(false)]
    [TestCase(true)]
    public void TryGetFullPath_LimitsDepthEvenWhenAncestorsAreCached(bool cacheAncestors)
    {
        var cache = new NetGuidCache();
        cache.SetNetGuidPath(3, "Root");
        for (uint index = 1; index <= 16; index++)
        {
            cache.SetNetGuidPath(3 + index * 2, $"Child{index}", new NetworkGuid(1 + index * 2));
            if (cacheAncestors && index < 16)
            {
                Assert.That(cache.TryGetFullPath(3 + index * 2, out _), Is.True);
            }
        }

        Assert.That(cache.TryGetFullPath(35, out var beyondLimit), Is.False);
        Assert.That(beyondLimit, Is.Null);
        Assert.That(cache.TryGetFullPath(33, out var atLimit), Is.True);
        Assert.That(atLimit, Does.EndWith(".Child15"));
    }

    [Test]
    public void TryGetFullPath_ChangingOuterInvalidatesDescendantsAndDetectsCycles()
    {
        var cache = new NetGuidCache();
        cache.SetNetGuidPath(3, "FirstRoot");
        cache.SetNetGuidPath(5, "Child", new NetworkGuid(3));
        cache.SetNetGuidPath(7, "Inner", new NetworkGuid(5));
        Assert.That(cache.TryGetFullPath(7, out var original), Is.True);
        Assert.That(original, Is.EqualTo("FirstRoot.Child:Inner"));

        cache.SetNetGuidPath(9, "SecondRoot");
        cache.SetNetGuidPath(5, "Child", new NetworkGuid(9));
        Assert.That(cache.TryGetFullPath(7, out var changed), Is.True);
        Assert.That(changed, Is.EqualTo("SecondRoot.Child:Inner"));

        cache.SetNetGuidPath(9, "SecondRoot", new NetworkGuid(7));
        Assert.That(cache.TryGetFullPath(7, out var cyclic), Is.False);
        Assert.That(cyclic, Is.Null);
    }

    [Test]
    public void TryGetFullPath_StartsBelowDynamicOuter()
    {
        var cache = new NetGuidCache();
        cache.SetNetGuidPath(5, "FiringState", new NetworkGuid(10));
        cache.SetNetGuidPath(7, "Inner", new NetworkGuid(5));

        Assert.Multiple(() =>
        {
            Assert.That(cache.TryGetFullPath(5, out var firingState), Is.True);
            Assert.That(firingState, Is.EqualTo("FiringState"));
            Assert.That(cache.TryGetFullPath(7, out var inner), Is.True);
            Assert.That(inner, Is.EqualTo("FiringState.Inner"));
        });
    }

    [Test]
    public void TryGetFullPath_FollowsChangedOuter()
    {
        var cache = new NetGuidCache();
        cache.SetNetGuidPath(3, "/Game/Weapons/Pistol");
        cache.SetNetGuidPath(5, "Pistol_C", new NetworkGuid(3));
        _ = cache.TryGetFullPath(5, out _);

        cache.SetNetGuidPath(3, "/Game/Weapons/Rifle");

        Assert.Multiple(() =>
        {
            Assert.That(cache.TryGetFullPath(5, out var fullPath), Is.True);
            Assert.That(fullPath, Is.EqualTo("/Game/Weapons/Rifle.Pistol_C"));
        });
    }

    [Test]
    public void SetNetGuidPath_StoresOuterNetGuid()
    {
        var cache = new NetGuidCache();
        var outerNetGuid = new NetworkGuid(11);

        cache.SetNetGuidPath(17, "Default__Test_C", outerNetGuid);
        cache.SetNetGuidPath(outerNetGuid.Value, "/Game/Test.Test_C");

        Assert.Multiple(() =>
        {
            Assert.That(cache.TryGetOuterNetGuid(17, out var resolvedOuter), Is.True);
            Assert.That(resolvedOuter, Is.EqualTo(outerNetGuid));
            Assert.That(cache.TryGetOuterPath(17, out var outerPath), Is.True);
            Assert.That(outerPath, Is.EqualTo("/Game/Test.Test_C"));
        });
    }

    [Test]
    public void Clear_RemovesAllState()
    {
        var cache = new NetGuidCache();
        cache.AddExportGroup(CreateGroup());
        cache.SetNetGuidPath(17, "/Game/Test.Test_C");
        Assert.That(cache.TryGetFullPath(17, out _), Is.True);

        cache.Clear();

        Assert.Multiple(() =>
        {
            Assert.That(cache.ExportGroupsByPath, Is.Empty);
            Assert.That(cache.ExportGroupsByPathIndex, Is.Empty);
            Assert.That(cache.PathByNetGuid, Is.Empty);
            Assert.That(cache.OuterNetGuidByNetGuid, Is.Empty);
            Assert.That(cache.TryGetFullPath(17, out var fullPath), Is.False);
            Assert.That(fullPath, Is.Null);
        });
    }

    private static NetFieldExportGroup CreateGroup() => new()
    {
        PathName = "/Game/Test.Test_C",
        PathNameIndex = 7,
        NetFieldExports = new NetFieldExport?[2],
    };
}
